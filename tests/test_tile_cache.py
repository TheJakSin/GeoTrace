from io import BytesIO
import json
import os
from threading import Event
import time

import numpy as np
from PIL import Image
import pytest

from geotrace import basemap
from geotrace.basemap import BasemapLoader, RenderCancelled, load_yandex_tile
from geotrace.tile_cache import tile_path, prune_yandex_cache, response_expiry, MAX_AGE


def downloader(monkeypatch, controls='max-age=3600', cancel=None):
    data = BytesIO()
    Image.new('RGBA', (256, 256), (23, 91, 150, 180)).save(data, 'PNG')
    calls = []
    def fetch(request, **kwargs):
        calls.append(True)
        response = BytesIO(data.getvalue())
        response.headers = {'Cache-Control': controls}
        if cancel is not None:
            cancel.set()
        return response
    monkeypatch.setattr(basemap, 'urlopen', fetch)
    return calls


def test_yandex_tiles_persist_reuse_expire_and_keep_keys_out_of_cache(tmp_path, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(basemap.time, 'time', lambda: clock[0])
    calls = downloader(monkeypatch)
    a = load_yandex_tile('private-key-one', 10, 12, 14, Event(), cache=tmp_path)
    b = load_yandex_tile('private-key-one', 10, 12, 14, Event(), cache=tmp_path)
    assert len(calls) == 1
    np.testing.assert_array_equal(a, b)
    assert all('private-key' not in str(p) for p in tmp_path.rglob('*'))
    assert all(b'private-key' not in p.read_bytes() for p in tmp_path.rglob('*.json'))
    load_yandex_tile('private-key-two', 10, 12, 14, Event(), cache=tmp_path)
    assert len(calls) == 2  # Different credentials cannot reuse an old grant.
    clock[0] += 3700
    load_yandex_tile('private-key-one', 10, 12, 14, Event(), cache=tmp_path)
    assert len(calls) == 3


def test_corrupt_tile_recovers_and_explicit_refresh_bypasses_cache(tmp_path, monkeypatch):
    calls = downloader(monkeypatch)
    args = ('test-key', 10, 12, 14, Event())
    load_yandex_tile(*args, cache=tmp_path)
    tile_path(tmp_path, 'test-key', 10, 12, 14).write_bytes(b'broken')
    load_yandex_tile(*args, cache=tmp_path)
    assert len(calls) == 2
    load_yandex_tile(*args, cache=tmp_path, refresh=True)
    assert len(calls) == 3


@pytest.mark.parametrize('controls', ['no-store', 'no-cache', 'max-age=0'])
def test_response_cache_controls_are_respected(tmp_path, monkeypatch, controls):
    calls = downloader(monkeypatch, controls)
    policy = {}
    for _ in range(2):
        load_yandex_tile('test-key', 10, 12, 14, Event(), cache=tmp_path, policy=policy)
    assert len(calls) == 2 and not list(tmp_path.rglob('*.png'))
    assert policy['expires'] <= time.time()
    assert response_expiry({'Cache-Control': 'max-age=999999999'}, 1000) == 1000+MAX_AGE


def test_download_completed_during_hide_is_reusable(tmp_path, monkeypatch):
    cancel = Event()
    calls = downloader(monkeypatch, cancel=cancel)
    with pytest.raises(RenderCancelled):
        load_yandex_tile('test-key', 10, 12, 14, cancel, cache=tmp_path)
    load_yandex_tile('test-key', 10, 12, 14, Event(), cache=tmp_path)
    assert len(calls) == 1


def test_pruning_caps_disk_use_and_removes_expired_entries(tmp_path, monkeypatch):
    calls = downloader(monkeypatch)
    for x in (1, 2):
        load_yandex_tile('test-key', 10, x, 14, Event(), cache=tmp_path)
        os.utime(tile_path(tmp_path, 'test-key', 10, x, 14), (x, x))
    newer = tile_path(tmp_path, 'test-key', 10, 2, 14)
    limit = newer.stat().st_size+newer.with_suffix('.json').stat().st_size
    prune_yandex_cache(tmp_path, limit=limit)
    assert newer.exists() and len(list(tmp_path.rglob('*.png'))) == 1
    metadata = json.loads(newer.with_suffix('.json').read_text())
    prune_yandex_cache(tmp_path, now=metadata['expires']+1)
    assert not list(tmp_path.rglob('*.png'))


def test_ready_views_survive_disable_and_source_switch_without_rendering(tmp_path, monkeypatch):
    calls = []
    def render(spec, cache, folder, cancel):
        calls.append(spec)
        path = tmp_path/f'view-{len(calls)}.png'
        Image.new('RGBA', (4, 4)).save(path)
        return dict(path=str(path), rect=[0, 0, 4, 4], credit='', note='')
    monkeypatch.setattr(basemap, 'render_basemap', render)
    loader = BasemapLoader(tmp_path, tmp_path)
    def wait():
        for _ in range(100):
            result = loader.poll()
            if result:
                return result
            time.sleep(.005)
        pytest.fail('Render did not finish')
    try:
        first = dict(source='yandex', api_key='test-key', width=4)
        loader.request(first)
        output = wait()
        loader.request(None)
        loader.request(first)
        assert loader.poll()['path'] == output['path'] and len(calls) == 1
        loader.request(dict(source='eox2016', width=4))
        wait()
        loader.request(first)
        assert loader.poll()['cache_hit'] and len(calls) == 2
        loader.request(first, refresh=True)
        wait()
        assert len(calls) == 3 and calls[-1]['force_refresh']
    finally:
        loader.close()
