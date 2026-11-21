from io import BytesIO
from threading import Event
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from affine import Affine
import numpy as np
from PIL import Image
import pytest

from geotrace import basemap
from geotrace.basemap import WORLD, load_yandex_tile, render_online, tile_plan
from geotrace.project import VIEW_DEFAULTS, save_project, load_project
from test_core import sample


def test_yandex_request_uses_web_mercator_and_keeps_alpha(monkeypatch):
    buffer = BytesIO()
    Image.new('RGBA', (256, 256), (20, 90, 130, 100)).save(buffer, format='PNG')
    requests = []
    def fetch(request, **options):
        requests.append(request)
        assert options['context'].verify_mode != 0
        return BytesIO(buffer.getvalue())
    monkeypatch.setattr(basemap, 'urlopen', fetch)
    tile = load_yandex_tile('test-key', 18, 20, 30, Event())
    query = parse_qs(urlparse(requests[0].full_url).query)
    assert requests[0].full_url.startswith('https://tiles.api-maps.yandex.ru/v1/tiles/')
    assert query == dict(apikey=['test-key'], lang=['ru_RU'], x=['20'], y=['30'], z=['18'],
                         l=['map'], projection=['web_mercator'], scale=['1'], maptype=['map'])
    np.testing.assert_array_equal(tile[0, 0], [20, 90, 130, 100])


@pytest.mark.parametrize('status', [403, 429, 500])
def test_yandex_errors_hide_key_and_explain_service_failure(monkeypatch, status):
    def fetch(request, **options):
        raise HTTPError(request.full_url, status, 'server failure', {}, None)
    monkeypatch.setattr(basemap, 'urlopen', fetch)
    with pytest.raises(ValueError) as error:
        load_yandex_tile('private-test-key', 1, 0, 0, Event())
    message = str(error.value)
    assert 'Яндекс' in message or 'Яндекса' in message
    assert 'private-test-key' not in message and 'apikey' not in message


def test_yandex_render_has_separate_tiles_projection_and_credit(monkeypatch, tmp_path):
    step = WORLD/(2*256)
    spec = dict(source='yandex', api_key='test-key', crs='EPSG:3857', width=256, height=256,
                transform=list(Affine(step, 0, 0, 0, -step, WORLD/2))[:6])
    requests = []
    def yandex(key, z, x, y, cancel, **options):
        requests.append((key, z, x, y))
        return np.full((256, 256, 4), [130, 90, 20, 255], np.uint8)
    monkeypatch.setattr(basemap, 'load_yandex_tile', yandex)
    monkeypatch.setattr(basemap, 'load_tile', lambda *args: pytest.fail('Yandex must use its own source'))
    output, credit, note = render_online(spec, tmp_path, Event())
    assert requests == [('test-key', 1, 1, 0)]
    np.testing.assert_array_equal(output[:, 128, 128], [130, 90, 20, 255])
    assert credit == '© Яндекс · Условия использования' and note == 'Яндекс · схема'
    with pytest.raises(ValueError, match='ключ'):
        render_online(dict(spec, api_key=''), tmp_path, Event())
    detailed = dict(spec, transform=list(Affine(1, 0, 0, 0, -1, 1000))[:6])
    assert tile_plan(detailed, maximum_zoom=20)[0] > tile_plan(detailed)[0]


def test_yandex_choice_roundtrips_without_serializing_credentials(tmp_path):
    path = tmp_path/'map.geotrace'
    view = dict(VIEW_DEFAULTS, basemap_source='yandex', basemap_visible=True, api_key='private-test-key')
    save_project(path, sample(), view=view)
    restored = load_project(path)
    assert restored.view['basemap_source'] == 'yandex' and restored.view['basemap_visible']
    import zipfile
    with zipfile.ZipFile(path) as archive:
        metadata = archive.read('project.json')
    assert b'private-test-key' not in metadata and b'api_key' not in metadata
