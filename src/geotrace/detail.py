"""Read only a bounded source window for high-resolution display and printing."""
from collections import OrderedDict
from hashlib import blake2b
from pathlib import Path
import math

from affine import Affine
import numpy as np
import rasterio
from rasterio.windows import Window
from shapely.geometry import box

from .core import Raster, read_raster
from .raster_colors import read_rgb, _source_identity
from .spatial import DISPLAY_CRS, project_geometry

_verified = OrderedDict()


def source_window(working, bounds, side=3600):
    if not Path(working.path).is_file():
        return working
    try:
        with rasterio.open(working.path) as src:
            valid = working.source_valid if working.source_valid is not None else working.valid
            fingerprint = blake2b(working.rgb.tobytes()+valid.tobytes(), digest_size=16).digest()
            key = _source_identity(src), working.transform, working.rgb.shape, fingerprint
            if key not in _verified:
                check = read_raster(working.path)
                _verified[key] = (check.transform == working.transform and check.crs_wkt == working.crs_wkt
                                  and np.array_equal(check.rgb, working.rgb) and np.array_equal(check.valid, valid))
                while len(_verified) > 64:
                    _verified.popitem(last=False)
            if not _verified[key]:
                return working
            region = project_geometry(box(*bounds), DISPLAY_CRS, working.crs_wkt, densify=True)
            if not region.intersects(box(*src.bounds)):
                return None
            inverse = ~src.transform
            x0, y0, x1, y1 = region.bounds
            points = [inverse*(x, y) for x, y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
            left = max(0, math.floor(min(p[0] for p in points))-3)
            top = max(0, math.floor(min(p[1] for p in points))-3)
            right = min(src.width, math.ceil(max(p[0] for p in points))+3)
            bottom = min(src.height, math.ceil(max(p[1] for p in points))+3)
            if right <= left or bottom <= top:
                return None
            window = Window(left, top, right-left, bottom-top)
            ratio = min(1., side/max(window.width, window.height))
            w, h = max(1, round(window.width*ratio)), max(1, round(window.height*ratio))
            rgb = read_rgb(src, w, h, window=window)
            mask = src.dataset_mask(window=window, out_shape=(h, w)) > 0
            transform = src.window_transform(window)*Affine.scale(window.width/w, window.height/h)
            # The source window must respect a manually selected map frame too.
            if working.area is not None:
                from rasterio.features import rasterize
                mask &= rasterize([(working.area, 1)], out_shape=(h, w), transform=transform, dtype='uint8').astype(bool)
            return Raster(working.path, np.moveaxis(rgb, 0, -1), mask, transform, working.crs_wkt, working.crs_label, working.source_size)
    except (OSError, ValueError, rasterio.errors.RasterioError):
        return working
