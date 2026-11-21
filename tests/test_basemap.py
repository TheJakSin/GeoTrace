from io import BytesIO
from pathlib import Path
from threading import Event
import time

from affine import Affine
import numpy as np
from PIL import Image
import pytest
import rasterio

from geotrace import basemap
from geotrace.basemap import (BasemapLoader, WORLD, render_online, render_local,
                              load_tile, tile_plan, viewport_spec)
from geotrace.project import VIEW_DEFAULTS, save_project, load_project
from test_core import sample


def local_file(path, sixteen=False):
    y, x = np.indices((64, 96))
    rgb = np.stack((x*2+10, y*3+10, x+y+20))
    if sixteen:
        rgb = (rgb+300)*8
    dtype = 'uint16' if sixteen else 'uint8'
    rgb = rgb.astype(dtype)
    alpha = np.full((64, 96), 65535 if sixteen else 255, dtype)
    alpha[20:30, 30:40] = 0
    transform = Affine(30, 3, 1000000, 2, -30, 6000000)
    with rasterio.open(path, 'w', driver='GTiff', width=96, height=64, count=4,
                       dtype=dtype, crs='EPSG:3857', transform=transform) as dst:
        dst.write(rgb, indexes=[1, 2, 3])
        dst.write(alpha, 4)
        dst.colorinterp = (rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha)
    spec = dict(source='local', file=str(path), crs='EPSG:3857', transform=list(transform)[:6],
                width=96, height=64, rect=[0, 0, 96, 64])
    return spec, rgb, transform


def test_local_rotated_geotiff_keeps_rgb_alpha_and_registration(tmp_path):
    spec, rgb, transform = local_file(tmp_path/'снимок.tif')
    out, credit, _ = render_local(spec, Event())
    np.testing.assert_allclose(out[:3, 5:15, 5:15], rgb[:, 5:15, 5:15], atol=1)
    assert not out[3, 21:29, 31:39].any()
    assert out[3, 5:15, 5:15].min() == 255
    assert 'снимок.tif' in credit
    smaller = dict(spec, width=30, height=20, transform=list(transform*Affine.translation(45, 35))[:6])
    part, _, _ = render_local(smaller, Event())
    np.testing.assert_allclose(part[:3, 2:18, 2:28], out[:3, 37:53, 47:73], atol=1)
    with pytest.raises(ValueError, match='не найден'):
        render_local(dict(spec, file=str(tmp_path/'moved.tif')), Event())


def test_sixteen_bit_stretch_stays_fixed_when_panning(tmp_path):
    spec, _, transform = local_file(tmp_path/'16bit.tif', sixteen=True)
    full, _, _ = render_local(spec, Event())
    part, _, _ = render_local(dict(spec, width=30, height=20,
                                    transform=list(transform*Affine.translation(45, 35))[:6]), Event())
    np.testing.assert_allclose(part[:3, 2:18, 2:28], full[:3, 37:53, 47:73], atol=1)
    assert full[:3].max() > 200 and full[:3].min() < 30


def test_online_tile_registration_and_bounded_requests(monkeypatch, tmp_path):
    step = WORLD/(2*256)
    spec = dict(crs='EPSG:3857', width=256, height=256,
                transform=list(Affine(step, 0, 0, 0, -step, WORLD/2))[:6])
    assert tile_plan(spec) == (1, (1, 0, 1, 0))
    monkeypatch.setattr(basemap, 'load_tile', lambda cache, z, x, y, cancel:
                        np.full((256, 256, 3), [x*30, y*40, z*50], np.uint8))
    out, _, _ = render_online(spec, tmp_path, Event())
    np.testing.assert_array_equal(out[:, 128, 128], [30, 0, 50, 255])
    large = dict(spec, width=1600, height=1600,
                 transform=list(Affine(WORLD/1600, 0, -WORLD/2, 0, -WORLD/1600, WORLD/2))[:6])
    _, (x0, y0, x1, y1) = tile_plan(large)
    assert (x1-x0+1)*(y1-y0+1) <= 64


def test_tile_cache_reuses_and_recovers_a_corrupt_tile(monkeypatch, tmp_path):
    image = Image.new('RGB', (256, 256), (20, 100, 60))
    data = BytesIO()
    image.save(data, format='JPEG')
    calls = []
    def download(request, **kwargs):
        assert request.full_url.startswith('https://tiles.maps.eox.at/')
        assert kwargs['context'].verify_mode != 0
        calls.append(request.full_url)
        return BytesIO(data.getvalue())
    monkeypatch.setattr(basemap, 'urlopen', download)
    load_tile(tmp_path, 2, 1, 1, Event())
    load_tile(tmp_path, 2, 1, 1, Event())
    assert len(calls) == 1
    (tmp_path/'tile-2-1-1.jpg').write_bytes(b'broken')
    load_tile(tmp_path, 2, 1, 1, Event())
    assert len(calls) == 2


def test_cancelled_request_returning_to_same_view_is_restarted(monkeypatch, tmp_path):
    started, released = Event(), Event()
    calls = []
    def render(spec, cache, folder, cancel):
        calls.append(spec['id'])
        if len(calls) == 1:
            started.set()
            assert released.wait(2)
        path = tmp_path/f'frame-{len(calls)}.png'
        path.write_bytes(b'frame')
        return dict(path=str(path), rect=[spec['id'], 0, 1, 1], credit='', note='')
    monkeypatch.setattr(basemap, 'render_basemap', render)
    loader = BasemapLoader(tmp_path, tmp_path)
    try:
        loader.request(dict(id=1))
        assert started.wait(2)
        loader.request(dict(id=2))
        loader.request(dict(id=1))
        released.set()
        output = None
        for _ in range(200):
            output = loader.poll()
            if output:
                break
            time.sleep(.005)
        assert output and output['rect'][0] == 1
        assert calls == [1, 1]
        assert not (tmp_path/'frame-1.png').exists()
    finally:
        released.set()
        loader.close()


def test_viewport_cap_and_project_source_persistence(tmp_path):
    raster = sample()
    view = dict(VIEW_DEFAULTS, basemap_visible=True, basemap_source='local', basemap_file='/my/snapshot.tif')
    spec = viewport_spec(raster, view, 10, 12, 30, 20, 6000, 4000)
    assert max(spec['width'], spec['height']) <= 1600
    assert spec['rect'] == [10, 12, 30, 20]
    assert Affine(*spec['transform']) * (0, 0) == raster.transform * (10, 12)
    path = tmp_path/'map.geotrace'
    save_project(path, raster, view=view)
    loaded = load_project(path)
    assert loaded.view == view


def test_context_extends_registered_grid_and_bounds_work_for_rotated_maps():
    from dataclasses import replace
    raster = replace(sample(), transform=Affine(30, 3, 1000000, 2, -30, 6000000))
    spec = viewport_spec(raster, VIEW_DEFAULTS, -100, -100, 300, 300, 4000, 3000)
    assert spec['rect'] == [-8, -5, 116, 70]
    target = Affine(*spec['transform'])
    np.testing.assert_allclose(target*(0, 0), raster.transform*(-8, -5))
    np.testing.assert_allclose(target*(spec['width'], spec['height']), raster.transform*(108, 65))
    assert max(spec['width'], spec['height']) <= 1600
    assert viewport_spec(raster, VIEW_DEFAULTS, 120, 0, 20, 10, 200, 100) is None
