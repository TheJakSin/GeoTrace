"""Bounded satellite rendering into the existing raster grid."""
from collections import deque, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from io import BytesIO
import math
import hashlib
import json
import os
from pathlib import Path
import ssl
import time
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import uuid

from affine import Affine
import certifi
import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject, transform_bounds
from .tile_cache import (MAX_AGE, tile_path, response_expiry, cached_expiry,
                         store_tile, discard, prune_yandex_cache)

WORLD = 40075016.68557849
TILE_SIZE = 256
CONTEXT_MARGIN = .08
EOX_URL = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg'
YANDEX_URL = 'https://tiles.api-maps.yandex.ru/v1/tiles/'
YANDEX_CREDIT = '© Яндекс · Условия использования'
EOX_CREDIT = 'EOxCloudless · cloudless.eox.at · EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016)'


class RenderCancelled(Exception):
    pass


def checkpoint(cancel):
    if cancel.is_set():
        raise RenderCancelled()


def viewport_spec(raster, view, x, y, width, height, pixels_w, pixels_h):
    h, w = raster.valid.shape
    # Extend the registered grid beyond the source, without extending the map's
    # processing mask or allocating a larger source raster.
    pad_x, pad_y = math.ceil(w*CONTEXT_MARGIN), math.ceil(h*CONTEXT_MARGIN)
    left, top = max(-pad_x, math.floor(x)), max(-pad_y, math.floor(y))
    right, bottom = min(w+pad_x, math.ceil(x+width)), min(h+pad_y, math.ceil(y+height))
    if right <= left or bottom <= top:
        return None
    scale = min(1., 1600/max(1, pixels_w, pixels_h))
    out_w, out_h = max(1, round(pixels_w*scale)), max(1, round(pixels_h*scale))
    transform = raster.transform * Affine.translation(left, top) * Affine.scale((right-left)/out_w, (bottom-top)/out_h)
    return dict(source=view['basemap_source'], file=view['basemap_file'], crs=raster.crs_wkt,
                transform=list(transform)[:6], width=out_w, height=out_h,
                rect=[left, top, right-left, bottom-top])


def _tile_range(bounds, zoom):
    left, bottom, right, top = bounds
    count = 2**zoom
    step = WORLD/count
    x0 = max(0, min(count-1, math.floor((left+WORLD/2)/step)))
    x1 = max(0, min(count-1, math.floor((right+WORLD/2-1e-8)/step)))
    y0 = max(0, min(count-1, math.floor((WORLD/2-top)/step)))
    y1 = max(0, min(count-1, math.floor((WORLD/2-bottom-1e-8)/step)))
    return x0, y0, x1, y1


def tile_plan(spec, maximum_zoom=14):
    transform = Affine(*spec['transform'])
    corners = [transform * point for point in [(0, 0), (spec['width'], 0),
                                                (0, spec['height']), (spec['width'], spec['height'])]]
    coords = np.asarray(corners)
    bounds = transform_bounds(spec['crs'], 'EPSG:3857', *coords.min(axis=0), *coords.max(axis=0), densify_pts=21)
    left, bottom, right, top = bounds
    if not np.isfinite(bounds).all() or right <= left or top <= bottom:
        raise ValueError('Не удалось определить область подложки.')
    if right < -WORLD/2 or left > WORLD/2 or top < -WORLD/2 or bottom > WORLD/2:
        raise ValueError('Подложка недоступна для этой области.')
    bounds = (max(-WORLD/2, left), max(-WORLD/2, bottom), min(WORLD/2, right), min(WORLD/2, top))
    resolution = max((right-left)/spec['width'], (top-bottom)/spec['height'])
    zoom = max(0, min(maximum_zoom, math.ceil(math.log2(WORLD/(TILE_SIZE*max(resolution, 1e-5))))))
    tiles = _tile_range(bounds, zoom)
    while (tiles[2]-tiles[0]+1)*(tiles[3]-tiles[1]+1) > 64 and zoom:
        zoom -= 1
        tiles = _tile_range(bounds, zoom)
    return zoom, tiles


def prune_cache(folder, limit=128*1024*1024):
    entries = []
    for path in folder.glob('tile-*.jpg'):
        try:
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
        except FileNotFoundError:
            continue
    size = sum(entry[1] for entry in entries)
    for _, length, path in sorted(entries):
        if size <= limit:
            break
        path.unlink(missing_ok=True)
        size -= length


def load_tile(cache, zoom, x, y, cancel):
    checkpoint(cancel)
    path = cache/f'tile-{zoom}-{x}-{y}.jpg'
    if path.is_file():
        try:
            with Image.open(path) as image:
                if image.size != (TILE_SIZE, TILE_SIZE):
                    raise ValueError('Размер тайла не соответствует источнику.')
                image.load()
                pixels = np.asarray(image.convert('RGB'))
            os.utime(path, None)
            return pixels
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    request = Request(EOX_URL.format(z=zoom, x=x, y=y), headers={'User-Agent': 'GeoTrace/0.2 (desktop GIS)'})
    try:
        with urlopen(request, timeout=10, context=ssl.create_default_context(cafile=certifi.where())) as response:
            data = response.read(2*1024*1024+1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise ValueError(f'Источник снимков вернул HTTP {exc.code}. Попробуйте обновить слой позже.') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError('Не удалось загрузить спутниковые снимки. Проверьте подключение к интернету.') from exc
    checkpoint(cancel)
    if len(data) > 2*1024*1024:
        raise ValueError('Источник вернул слишком большой тайл.')
    with Image.open(BytesIO(data)) as image:
        if image.size != (TILE_SIZE, TILE_SIZE):
            raise ValueError('Источник вернул изображение неожиданного размера.')
        image.load()
        pixels = np.asarray(image.convert('RGB'))
    temporary = path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return pixels


def load_yandex_tile(api_key, zoom, x, y, cancel, cache=None, policy=None, refresh=False, pace=None):
    checkpoint(cancel)
    now = time.time()
    path = tile_path(cache, api_key, zoom, x, y) if cache is not None else None
    if path is not None and path.is_file() and not refresh:
        expires = cached_expiry(path, now)
        if expires is not None:
            try:
                with Image.open(path) as image:
                    if image.size != (TILE_SIZE, TILE_SIZE):
                        raise ValueError('Некорректный тайл в кэше.')
                    image.load()
                    pixels = np.asarray(image.convert('RGBA'))
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                if policy is not None:
                    policy['expires'] = min(policy.get('expires', expires), expires)
                return pixels
            except (OSError, ValueError):
                pass
        discard(path)
    if pace is not None:
        # Cached tiles incur no rate-limit delay. Pace only real API calls.
        if cancel.wait(max(0., pace.get('next', 0.)-time.monotonic())):
            raise RenderCancelled()
        pace['next'] = time.monotonic()+.05
    query = urlencode(dict(apikey=api_key, lang='ru_RU', x=x, y=y, z=zoom, l='map',
                           projection='web_mercator', scale=1, maptype='map'))
    request = Request(YANDEX_URL+'?'+query, headers={'User-Agent': 'GeoTrace/0.2 (desktop GIS)'})
    try:
        with urlopen(request, timeout=10, context=ssl.create_default_context(cafile=certifi.where())) as response:
            data = response.read(2*1024*1024+1)
            expires = response_expiry(getattr(response, 'headers', {}), now)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError('Яндекс отклонил ключ. Проверьте доступ к пакету Tiles API и ограничения ключа.') from None
        if exc.code == 429:
            raise ValueError('Достигнут лимит запросов Яндекса. Обновите подложку позже.') from None
        raise ValueError(f'Яндекс вернул HTTP {exc.code}. Обновите подложку позже.') from None
    except (URLError, TimeoutError, OSError):
        raise ValueError('Не удалось загрузить схему Яндекса. Проверьте подключение к интернету.') from None
    if len(data) > 2*1024*1024:
        raise ValueError('Яндекс вернул слишком большой тайл.')
    try:
        with Image.open(BytesIO(data)) as image:
            if image.size != (TILE_SIZE, TILE_SIZE):
                raise ValueError('Яндекс вернул изображение неожиданного размера.')
            image.load()
            pixels = np.asarray(image.convert('RGBA'))
    except OSError:
        raise ValueError('Ответ Яндекса не содержит корректного изображения карты.') from None
    # A completed response remains useful even if the viewport changed while
    # downloading. Cache it before stopping the obsolete render.
    if path is not None:
        try:
            store_tile(path, data, expires, now)
        except OSError:
            pass  # A read-only/full cache must not prevent displaying the map.
    if policy is not None:
        policy['expires'] = min(policy.get('expires', expires), expires)
    checkpoint(cancel)
    return pixels


def render_online(spec, cache, cancel, policy=None):
    yandex = spec.get('source') == 'yandex'
    if yandex and not spec.get('api_key'):
        raise ValueError('Введите ключ пакета Tiles API в настройках подложки Яндекса.')
    zoom, (x0, y0, x1, y1) = tile_plan(spec, maximum_zoom=20 if yandex else 14)
    mosaic = np.zeros((4, (y1-y0+1)*TILE_SIZE, (x1-x0+1)*TILE_SIZE), np.uint8)
    missing = 0
    pace = {}
    for y in range(y0, y1+1):
        for x in range(x0, x1+1):
            if yandex:
                pixels = load_yandex_tile(spec['api_key'], zoom, x, y, cancel, cache=cache,
                                          policy=policy, refresh=bool(spec.get('force_refresh')), pace=pace)
            else:
                pixels = load_tile(cache, zoom, x, y, cancel)
            if pixels is None:
                missing += 1
                continue
            row, col = (y-y0)*TILE_SIZE, (x-x0)*TILE_SIZE
            mosaic[:3, row:row+TILE_SIZE, col:col+TILE_SIZE] = pixels[:, :, :3].transpose(2, 0, 1)
            mosaic[3, row:row+TILE_SIZE, col:col+TILE_SIZE] = pixels[:, :, 3] if yandex else 255
    checkpoint(cancel)
    if not mosaic[3].any():
        raise ValueError('Для выбранной области подложка этого источника недоступна.')
    step = WORLD/(2**zoom*TILE_SIZE)
    source_transform = Affine(step, 0, -WORLD/2+x0*TILE_SIZE*step, 0, -step, WORLD/2-y0*TILE_SIZE*step)
    output = np.zeros((4, spec['height'], spec['width']), np.uint8)
    for channel in range(4):
        reproject(mosaic[channel], output[channel], src_transform=source_transform, src_crs='EPSG:3857',
                  dst_transform=Affine(*spec['transform']), dst_crs=spec['crs'],
                  resampling=Resampling.bilinear if channel < 3 else Resampling.nearest)
    if yandex:
        return output, YANDEX_CREDIT, 'Яндекс · схема'
    return output, EOX_CREDIT, f'EOX · 2016 · нет снимков для {missing} тайлов' if missing else 'EOX · снимки 2016 года'


def _rgb_bands(source):
    if source.count < 3 or source.crs is None or source.transform == Affine.identity():
        raise ValueError('Для подложки нужен цветной GeoTIFF с географической привязкой.')
    colors = list(source.colorinterp)
    if all(c in colors for c in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)):
        return [colors.index(c)+1 for c in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)]
    return [1, 2, 3]


@lru_cache(maxsize=8)
def local_style(path, stamp, size):
    with rasterio.open(path) as source:
        bands = _rgb_bands(source)
        if all(source.dtypes[i-1] == 'uint8' for i in bands):
            return bands, None
        sample = source.read(bands, out_shape=(3, 256, 256), masked=True, resampling=Resampling.nearest)
        limits = []
        for channel in sample:
            values = channel.compressed()
            values = values[np.isfinite(values)]
            if not len(values):
                raise ValueError('В снимке отсутствуют допустимые значения RGB.')
            low, high = np.percentile(values, [2, 98])
            limits.append((float(low), float(max(low+1, high))))
        return bands, limits


def render_local(spec, cancel):
    path = Path(spec['file'])
    if not path.is_file():
        raise ValueError('Файл подложки не найден. Выберите GeoTIFF повторно.')
    stat = path.stat()
    bands, limits = local_style(str(path), stat.st_mtime_ns, stat.st_size)
    checkpoint(cancel)
    with rasterio.open(path) as source:
        options = dict(crs=spec['crs'], transform=Affine(*spec['transform']), width=spec['width'],
                       height=spec['height'], resampling=Resampling.bilinear)
        # Existing alpha takes precedence. Otherwise the VRT creates alpha for
        # NoData and the extent outside the source, without reading the whole file.
        if ColorInterp.alpha not in source.colorinterp:
            options['add_alpha'] = True
        with WarpedVRT(source, **options) as warped:
            data = warped.read(bands).astype(np.float32)
            alpha = warped.dataset_mask()
    if limits is not None:
        for i, (low, high) in enumerate(limits):
            data[i] = (data[i]-low)*255/(high-low)
    checkpoint(cancel)
    output = np.concatenate((np.nan_to_num(data).clip(0, 255).astype(np.uint8), alpha[None]), axis=0)
    return output, f'Подложка: {path.name}', 'Локальный GeoTIFF'


def render_basemap(spec, cache, folder, cancel):
    checkpoint(cancel)
    policy = dict(expires=time.time()+MAX_AGE)
    if spec['source'] in ('eox2016', 'yandex'):
        cache = Path(cache)
        if spec['source'] == 'eox2016':
            cache.mkdir(parents=True, exist_ok=True)
            prune_cache(cache)
        if spec['source'] == 'yandex':
            prune_yandex_cache(cache)
        rgba, credit, note = render_online(spec, cache, cancel, policy=policy)
        if spec['source'] == 'yandex':
            prune_yandex_cache(cache)
    elif spec['source'] == 'local':
        rgba, credit, note = render_local(spec, cancel)
    else:
        raise ValueError('Неизвестный источник подложки.')
    checkpoint(cancel)
    path = Path(folder)/('basemap-'+uuid.uuid4().hex+'.png')
    Image.fromarray(rgba.transpose(1, 2, 0)).save(path)
    return dict(path=str(path), rect=spec['rect'], credit=credit, note=note, expires=policy['expires'])


class BasemapLoader:
    """One active render and the most recent requested view; stale results are discarded."""
    def __init__(self, cache, folder):
        self.cache, self.folder = cache, folder
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='basemap')
        self.future = None
        self.cancel = Event()
        self.latest = None
        self.active = None
        self.generation = 0
        self.active_generation = 0
        self.frames = deque()
        self.cached_frames = OrderedDict()
        self.ready = None
        self.satisfied = True
        self.refresh = False

    @staticmethod
    def frame_key(spec):
        identity = dict(spec)
        if spec.get('source') == 'local':
            try:
                stat = Path(spec['file']).stat()
                identity['file_stamp'] = [stat.st_mtime_ns, stat.st_size]
            except OSError:
                identity['file_stamp'] = None
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def remember(self, spec, output):
        if output.get('expires', time.time()+1) <= time.time():
            self.frames.append(Path(output['path']))
        else:
            key = self.frame_key(spec)
            previous = self.cached_frames.pop(key, None)
            if previous and previous['path'] != output['path']:
                Path(previous['path']).unlink(missing_ok=True)
            self.cached_frames[key] = output
        while len(self.frames) > 2:
            self.frames.popleft().unlink(missing_ok=True)
        size = sum(Path(item['path']).stat().st_size for item in self.cached_frames.values()
                   if Path(item['path']).exists())
        while len(self.cached_frames) > 8 or (size > 32*1024*1024 and len(self.cached_frames) > 1):
            _, item = self.cached_frames.popitem(last=False)
            path = Path(item['path'])
            size -= path.stat().st_size if path.exists() else 0
            path.unlink(missing_ok=True)

    def request(self, spec, refresh=False):
        if spec == self.latest and not refresh:
            return
        self.latest = spec
        self.generation += 1
        self.cancel.set()
        self.ready = None
        self.satisfied = spec is None
        self.refresh = refresh
        if spec is not None and not refresh:
            key = self.frame_key(spec)
            cached = self.cached_frames.get(key)
            if cached and Path(cached['path']).is_file() and cached.get('expires', time.time()+1) > time.time():
                self.cached_frames.move_to_end(key)
                self.ready = dict(cached, cache_hit=True)
                self.satisfied = True
        if self.future is None and spec is not None and not self.satisfied:
            self._start()

    def _start(self):
        self.active = self.latest
        self.active_generation = self.generation
        self.cancel = Event()
        spec = dict(self.active, force_refresh=True) if self.refresh else self.active
        self.future = self.pool.submit(render_basemap, spec, self.cache, self.folder, self.cancel)

    def poll(self):
        if self.ready is not None:
            result, self.ready = self.ready, None
            return result
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        result = None
        try:
            output = future.result()
            if self.active_generation == self.generation:
                result = output
                self.satisfied = True
                self.remember(self.active, output)
            else:
                Path(output['path']).unlink(missing_ok=True)
        except RenderCancelled:
            pass
        except Exception as exc:
            if self.active_generation == self.generation:
                result = dict(error=str(exc))
        if self.active_generation != self.generation and self.latest is not None and not self.satisfied:
            self._start()
        return result

    def close(self):
        self.cancel.set()
        self.latest = None
        self.pool.shutdown(wait=False, cancel_futures=True)
