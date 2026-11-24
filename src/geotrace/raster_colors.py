"""Bounded decoding of display colours from georeferenced TIFFs."""
from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.windows import Window


_byte_ranges = OrderedDict()


def _byte_values_in_uint16(source):
    """Accept byte RGB stored in wider channels, without guessing a stretch.

    Some published maps reserve 256 for NoData. Check all valid source pixels,
    not a sample which could miss a small high-valued area. Reuse the check
    within the worker while the local source identity and timestamp match.
    """
    key = _source_identity(source)
    if key in _byte_ranges:
        _byte_ranges.move_to_end(key)
        return _byte_ranges[key]
    compatible = True
    for y in range(0, source.height, 1024):
        for x in range(0, source.width, 1024):
            window = Window(x, y, min(1024, source.width-x), min(1024, source.height-y))
            valid = source.dataset_mask(window=window) > 0
            if valid.any() and np.any(source.read([1, 2, 3], window=window)[:, valid] > 255):
                compatible = False
                break
        if not compatible:
            break
    if key is not None:
        _byte_ranges[key] = compatible
        while len(_byte_ranges) > 8:
            _byte_ranges.popitem(last=False)
    return compatible


_palette_files = OrderedDict()
_palette_folder = None
_PALETTE_CACHE_BYTES = 256 * 1024**2


def _source_identity(source):
    try:
        files = [Path(source.name), Path(source.name+'.msk'), Path(source.name+'.aux.xml')]
        identity = tuple((str(p), p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                         for p in files if p.is_file())
        if not identity:
            return None
        return identity, source.width, source.height, source.count, source.nodata
    except OSError:
        return None


def _expand_palette(source):
    global _palette_folder
    if _palette_folder is None:
        _palette_folder = TemporaryDirectory(prefix='geotrace-colours-')
    path = Path(_palette_folder.name)/(uuid4().hex+'.tif')
    colours = source.colormap(1)
    table = np.zeros((max(colours)+1, 3), np.uint8)
    for index, value in colours.items():
        table[index] = value[:3]
    try:
        with rasterio.open(path, 'w', driver='GTiff', width=source.width,
                           height=source.height, count=4, dtype='uint8',
                           crs=source.crs, transform=source.transform, tiled=True,
                           blockxsize=512, blockysize=512, compress='deflate',
                           zlevel=1, predictor=2, BIGTIFF='IF_SAFER') as expanded:
            expanded.colorinterp = (ColorInterp.red, ColorInterp.green,
                                    ColorInterp.blue, ColorInterp.alpha)
            for y in range(0, source.height, 512):
                for x in range(0, source.width, 512):
                    window = Window(x, y, min(512, source.width-x), min(512, source.height-y))
                    indices = source.read(1, window=window)
                    rgba = np.empty((4, int(window.height), int(window.width)), np.uint8)
                    rgba[:3] = np.moveaxis(table[indices], -1, 0)
                    rgba[3] = source.dataset_mask(window=window)
                    expanded.write(rgba, window=window)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _palette_rgb(source, width, height, window=None):
    # Decode native indices in bounded blocks before RGB resampling. A real
    # temporary RGBA dataset avoids native VRT mask/colour-table pointer issues
    # seen across GDAL builds. Reuse it for overview and detailed analysis.
    key = _source_identity(source)
    cached = _palette_files.get(key)
    if cached is not None and not cached[0].is_file():
        del _palette_files[key]
        cached = None
    with rasterio.Env(GDAL_CACHEMAX=32_000_000):
        if cached is None:
            path = _expand_palette(source)
            size = path.stat().st_size
            keep = key is not None and size <= _PALETTE_CACHE_BYTES
            if keep:
                while _palette_files and (len(_palette_files) >= 2 or
                        sum(item[1] for item in _palette_files.values())+size > _PALETTE_CACHE_BYTES):
                    _, (old, _) = _palette_files.popitem(last=False)
                    old.unlink(missing_ok=True)
                _palette_files[key] = path, size
        else:
            path, _ = cached
            keep = True
            _palette_files.move_to_end(key)
        try:
            with rasterio.open(path) as expanded:
                return expanded.read([1, 2, 3], out_shape=(3, height, width),
                                     resampling=Resampling.bilinear, window=window)
        finally:
            if not keep:
                path.unlink(missing_ok=True)


def read_rgb(source, width, height, window=None):
    """Return byte RGB; preserve the established RGB/RGBA reading path exactly."""
    if source.count == 1 and source.colorinterp[0] == ColorInterp.palette:
        return _palette_rgb(source, width, height, window)
    if source.count >= 3:
        types = source.dtypes[:3]
        if all(kind == 'uint8' for kind in types):
            return source.read([1, 2, 3], out_shape=(3, height, width), resampling=Resampling.bilinear, window=window)
        if all(kind == 'uint16' for kind in types) and _byte_values_in_uint16(source):
            values = source.read([1, 2, 3], out_shape=(3, height, width), resampling=Resampling.bilinear, window=window)
            return np.minimum(values, 255).astype(np.uint8)
    raise ValueError('Нужна цветная RGB/RGBA-карта с цветами 0–255 или TIFF с цветовой таблицей.')
