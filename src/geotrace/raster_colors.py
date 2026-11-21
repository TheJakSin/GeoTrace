"""Bounded decoding of display colours from georeferenced TIFFs."""
from collections import OrderedDict
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from affine import Affine
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window


_byte_ranges = OrderedDict()


def _byte_values_in_uint16(source):
    """Accept byte RGB stored in wider channels, without guessing a stretch.

    Some published maps reserve 256 for NoData. Check all valid source pixels,
    not a sample which could miss a small high-valued area. Reuse the check
    within the worker while the local source identity and timestamp match.
    """
    key = None
    try:
        stat = Path(source.name).stat()
        key = (source.name, stat.st_ino, stat.st_size, stat.st_mtime_ns,
               source.width, source.height, source.count)
    except OSError:
        pass
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


def _palette_rgb(source, width, height):
    # Expand native palette entries before resampling. Direct VRT RasterIO
    # with out_shape may interpolate the *indices*, inventing unrelated hues.
    # Private GDAL source handles avoid sharing a dataset with other readers
    # or worker threads in the application.
    filename = escape(source.name)
    colours = source.colormap(1)
    bands = []
    for channel, name in enumerate(('Red', 'Green', 'Blue'), 1):
        # Copy palette values into the VRT lookup table. This avoids retaining
        # a native colour-table pointer across the nested warp reader.
        lookup = ','.join(f'{index}:{value[channel-1]}' for index, value in sorted(colours.items()))
        bands.append(
            f'<VRTRasterBand dataType="Byte" band="{channel}"><ColorInterp>{name}</ColorInterp>'
            f'<ComplexSource><SourceFilename relativeToVRT="0" shared="0">{filename}</SourceFilename>'
            '<SourceBand>1</SourceBand>'
            f'<LUT>{lookup}</LUT>'
            f'<SrcRect xOff="0" yOff="0" xSize="{source.width}" ySize="{source.height}"/>'
            f'<DstRect xOff="0" yOff="0" xSize="{source.width}" ySize="{source.height}"/>'
            '</ComplexSource></VRTRasterBand>')
    bands.append(
        '<VRTRasterBand dataType="Byte" band="4"><ColorInterp>Alpha</ColorInterp>'
        f'<SimpleSource><SourceFilename relativeToVRT="0" shared="0">{filename}</SourceFilename>'
        '<SourceBand>mask,1</SourceBand>'
        f'<SrcRect xOff="0" yOff="0" xSize="{source.width}" ySize="{source.height}"/>'
        f'<DstRect xOff="0" yOff="0" xSize="{source.width}" ySize="{source.height}"/>'
        '</SimpleSource></VRTRasterBand>')
    # Colour decoding is an image-grid operation. A north-up private grid
    # avoids platform-dependent warp clipping of rotated georeferenced edges.
    # The caller retains the source CRS and affine for the resulting raster.
    image_transform = Affine(1, 0, 0, 0, -1, source.height)
    xml = (f'<VRTDataset rasterXSize="{source.width}" rasterYSize="{source.height}">'
           '<SRS>EPSG:3857</SRS>'
           f'<GeoTransform>{",".join(map(str, image_transform.to_gdal()))}</GeoTransform>'
           f'{"".join(bands)}</VRTDataset>')
    with MemoryFile(xml.encode('utf-8')) as memory, memory.open() as expanded:
        transform = image_transform * Affine.scale(source.width/width, source.height/height)
        with WarpedVRT(expanded, crs='EPSG:3857', transform=transform, width=width,
                       height=height, src_alpha=4, resampling=Resampling.bilinear,
                       warp_mem_limit=32) as decoded:
            return decoded.read([1, 2, 3])


def read_rgb(source, width, height):
    """Return byte RGB; preserve the established RGB/RGBA reading path exactly."""
    if source.count == 1 and source.colorinterp[0] == ColorInterp.palette:
        return _palette_rgb(source, width, height)
    if source.count >= 3:
        types = source.dtypes[:3]
        if all(kind == 'uint8' for kind in types):
            return source.read([1, 2, 3], out_shape=(3, height, width), resampling=Resampling.bilinear)
        if all(kind == 'uint16' for kind in types) and _byte_values_in_uint16(source):
            values = source.read([1, 2, 3], out_shape=(3, height, width), resampling=Resampling.bilinear)
            return np.minimum(values, 255).astype(np.uint8)
    raise ValueError('Нужна цветная RGB/RGBA-карта с цветами 0–255 или TIFF с цветовой таблицей.')
