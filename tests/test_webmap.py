import json
import importlib.util
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile
import subprocess
import sys

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from shapely.geometry import Polygon

from geotrace.app import Backend
from geotrace.basemap_config import DEFAULT_YANDEX_JS_API_KEY
from geotrace.editing import transform_geometry
from geotrace.project import save_project
from geotrace.region import set_area
from geotrace.webmap import project_context, WebMapServer
from test_core import sample


def test_project_context_keeps_wgs84_order_and_kml_holes():
    raster = sample()
    geometry = transform_geometry(Polygon([(8, 8), (88, 8), (88, 52), (8, 52)],
                                           [[(40, 20), (60, 20), (60, 40), (40, 40)]]), raster.transform)
    cropped = set_area(raster, geometry, 'Участок')
    context = project_context(cropped)
    x, y = raster.transform*(0, 0)
    np.testing.assert_allclose(context['outline'][0], [y, x])
    assert len(context['areas']) == 1 and len(context['areas'][0]) == 1+len(cropped.area.interiors)
    np.testing.assert_allclose(context['areas'][0][1][0], list(reversed(cropped.area.interiors[0].coords[0])))
    assert context['name'] == Path(raster.path).name
    assert project_context(None)['bounds'] is None


def test_loopback_page_only_serves_its_own_tokenised_read_only_resources(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'getfqdn', lambda *args: pytest.fail('Loopback must not query DNS'))
    server = WebMapServer('private-test-key')
    try:
        url = server.start()
        assert server.context['vectorRevision'] == 0
        assert server.context['rasterRevision'] == 0 and not server.context['rasterAvailable']
        assert 'private-test-key' not in url
        assert server.start() == url
        with urlopen(url) as response:
            page = response.read()
            assert response.headers['Cache-Control'] == 'no-store'
        assert b'private-test-key' not in page
        with urlopen(url.replace('index.html', 'config.json')) as response:
            assert json.load(response) == dict(apiKey='private-test-key')
        server.update(context=project_context(sample()))
        with urlopen(url.replace('index.html', 'project.json')) as response:
            assert json.load(response)['bounds'] is not None
        for suffix in ('../private.txt', 'unknown.json'):
            with pytest.raises(HTTPError) as error:
                urlopen(url.replace('index.html', suffix))
            assert error.value.code == 404
        with pytest.raises(HTTPError):
            urlopen(url.replace(server.token, 'wrong-token'))
    finally:
        server.close()
        server.close()


def test_web_key_is_separate_from_tiles_and_never_enters_project_state(tmp_path):
    application = QGuiApplication.instance() or QGuiApplication([])
    settings = QSettings(str(tmp_path/'web.ini'), QSettings.IniFormat)
    backend = Backend(settings=settings, recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    try:
        assert backend.web_key == DEFAULT_YANDEX_JS_API_KEY
        tiles_key = backend.yandex_key
        assert backend.setWebMapKey('personal-web-test-key')
        assert backend.yandex_key == tiles_key
        assert 'personal-web-test-key' not in str(backend.data)
        assert not backend.data['dirty']
        assert not backend.setWebMapKey('bad key')
        backend.reportWebMap('arbitrary unsanitised message')
        assert 'arbitrary' not in backend.data['webMapStatus']
        path = tmp_path/'map.geotrace'
        save_project(path, sample(), view=backend.view)
        with zipfile.ZipFile(path) as archive:
            assert b'personal-web-test-key' not in archive.read('project.json')
        assert backend.resetWebMapKey() and backend.web_key == DEFAULT_YANDEX_JS_API_KEY
        assert not settings.contains('webmap/yandexJsApiKey')
    finally:
        backend.timer.stop()
        backend.web_map.close()
        backend.basemap_loader.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        backend.temp.cleanup()


def test_browser_is_not_reloaded_by_editor_updates_or_switching_tabs(tmp_path):
    if importlib.util.find_spec('PySide6.QtWebEngineQuick') is None:
        pytest.skip('Qt WebEngine is not installed')
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('webmap_lifecycle_probe.py')), str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'loaded once' in result.stdout


def test_local_tiles_align_rotated_raster_preserve_mask_and_cache():
    from io import BytesIO
    from PIL import Image
    from rasterio.warp import transform
    from geotrace.webmap import RasterTiles
    raster=sample()
    tiles=RasterTiles(raster)
    z=12
    # Samples are well away from mask edges to separate projection from resampling.
    def pixel_at(px,py):
        wx,wy=raster.transform*(px,py)
        xs,ys=transform(raster.crs_wkt,'EPSG:3395',[wx],[wy])
        scale=256*(2**z)/(2*tiles.extent)
        gx=(xs[0]+tiles.extent)*scale
        gy=(tiles.extent-ys[0])*scale
        x,y=int(gx//256),int(gy//256)
        data=tiles.tile(z,x,y)
        assert tiles.tile(z,x+2**z,y) is data
        image=np.asarray(Image.open(BytesIO(data)))
        return image[int(gy%256),int(gx%256)]
    np.testing.assert_allclose(pixel_at(10,10),[230,180,80,255],atol=1)
    np.testing.assert_allclose(pixel_at(75,40),[110,180,210,255],atol=1)
    assert pixel_at(25,25)[3]==0
    assert pixel_at(-10,-10)[3]==0
    with pytest.raises(ValueError):tiles.tile(23,0,0)
    with pytest.raises(ValueError):tiles.tile(2,0,4)
    assert len(tiles.cache)<=128


def test_web_snapshots_vector_holes_versions_and_stale_tile_rejection():
    from test_editing import baseline
    from geotrace.editing import edit_geometry
    from io import BytesIO
    from PIL import Image
    server=WebMapServer('test')
    original=baseline()
    try:
        server.set_project(original.raster,original)
        url=server.start().replace('index.html','')
        context=json.load(urlopen(url+'project.json'))
        vectors=json.load(urlopen(url+'polygons.json'))
        assert context['polygonCount']==2 and context['rasterAvailable']
        assert len(vectors['features'])==2
        for feature,source in zip(vectors['features'],original.features):
            np.testing.assert_allclose(feature['geometry']['coordinates'][0][0],list(reversed(source['geometry'].exterior.coords[0])))
            assert len(feature['geometry']['coordinates'])==1+len(source['geometry'].interiors)
        tile_url=url+f"raster/{context['rasterRevision']}/0/0/0.png"
        with urlopen(tile_url) as response:
            assert Image.open(BytesIO(response.read())).size==(256,256)
            assert 'max-age' in response.headers['Cache-Control']
        tiles=server.tiles
        server.set_project(original.raster,original)
        assert server.context==context and server.tiles is tiles
        updated=edit_geometry(original,[f['properties']['object_id'] for f in original.features],'merge')
        server.set_project(original.raster,updated)
        assert server.context['polygonCount']==1
        assert server.context['rasterRevision']==context['rasterRevision'] and server.tiles is tiles
        assert server.context['vectorRevision']>context['vectorRevision']
        from dataclasses import replace
        cropped=replace(original.raster,valid=original.raster.valid.copy())
        server.set_project(cropped,None)
        with pytest.raises(HTTPError) as error:urlopen(tile_url)
        assert error.value.code==404
        server.set_project(None,None)
        assert not server.context['rasterAvailable'] and not server.vectors['features'] and server.tiles is None
    finally:server.close()
