import os

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.enums import ColorInterp

from geotrace.core import read_raster


TRANSFORM = Affine(2, .2, 300000, .1, -2, 3650000)


def test_palette_is_expanded_before_resampling_without_inventing_colours(tmp_path):
    path = tmp_path/'карта & цвета.tif'
    y, x = np.indices((128, 192))
    indices = np.where((x+y) % 2, 1, 200).astype(np.uint8)
    with rasterio.open(path, 'w', driver='GTiff', width=192, height=128, count=1,
                       dtype='uint8', crs='EPSG:26713', transform=TRANSFORM) as dst:
        dst.write(indices, 1)
        dst.write_colormap(1, {1: (255, 0, 0, 255), 100: (0, 255, 0, 255), 200: (0, 0, 255, 255)})
    actual = read_raster(path, max_side=24)
    assert actual.rgb.shape == (16, 24, 3) and actual.valid.all()
    assert actual.rgb[:, :, 1].max() == 0, 'Interpolating indices invents the green palette entry.'
    assert np.all((actual.rgb[:, :, 0] > 110) & (actual.rgb[:, :, 0] < 145))
    assert np.all((actual.rgb[:, :, 2] > 110) & (actual.rgb[:, :, 2] < 145))
    assert actual.transform == TRANSFORM * Affine.scale(8)
    assert actual.crs_label == 'EPSG:26713' and actual.source_size == (192, 128)


def test_palette_mask_and_transparent_hole_do_not_add_black_fringes(tmp_path):
    path = tmp_path/'palette.tif'
    indices = np.ones((80, 100), np.uint8)
    indices[20:40, 20:40] = 0
    with rasterio.open(path, 'w', driver='GTiff', width=100, height=80, count=1,
                       dtype='uint8', crs='EPSG:26713', transform=TRANSFORM, nodata=0) as dst:
        dst.write(indices, 1)
        dst.write_colormap(1, {0: (0, 0, 0, 0), 1: (235, 180, 120, 255)})
    actual = read_raster(path, max_side=50)
    assert (~actual.valid).sum() == 100
    assert np.all(np.abs(actual.rgb[actual.valid].astype(int)-[235, 180, 120]) <= 1)


def test_byte_rgb_in_wider_channels_keeps_nodata_and_valid_colours(tmp_path):
    path = tmp_path/'rgb16.tif'
    rgb = np.zeros((3, 60, 100), np.uint16)
    rgb[:] = np.array([230, 180, 80], np.uint16)[:, None, None]
    rgb[:, 20:30, 20:30] = 256
    with rasterio.open(path, 'w', driver='GTiff', width=100, height=60, count=3,
                       dtype='uint16', crs='EPSG:26713', transform=TRANSFORM, nodata=256) as dst:
        dst.write(rgb)
    actual = read_raster(path)
    assert actual.rgb.dtype == np.uint8 and (~actual.valid).sum() == 100
    np.testing.assert_array_equal(actual.rgb[actual.valid], np.tile([230, 180, 80], (5900, 1)))
    assert actual.transform == TRANSFORM


def test_uint16_range_check_cannot_miss_a_small_high_value_and_expires_on_change(tmp_path):
    path = tmp_path/'rgb16.tif'
    rgb = np.full((3, 128, 128), 200, np.uint16)
    with rasterio.open(path, 'w', driver='GTiff', width=128, height=128, count=3,
                       dtype='uint16', crs='EPSG:26713', transform=TRANSFORM) as dst:
        dst.write(rgb)
    assert read_raster(path, max_side=8).rgb.max() == 200
    before = path.stat()
    with rasterio.open(path, 'r+') as dst:
        rgb[:, 93, 101] = 4096
        dst.write(rgb)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns+1_000_000_000))
    with pytest.raises(ValueError, match='0–255'):
        read_raster(path, max_side=8)


def test_numeric_single_band_is_not_treated_as_a_colour_map(tmp_path):
    path = tmp_path/'numbers.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=20, height=20, count=1,
                       dtype='float32', crs='EPSG:26713', transform=TRANSFORM) as dst:
        dst.write(np.ones((20, 20), np.float32), 1)
    with pytest.raises(ValueError, match='цвет'):
        read_raster(path)


def test_palette_colours_do_not_depend_on_the_geographic_grid(tmp_path):
    indices = np.ones((80, 100), np.uint8)
    indices[20:40, 20:40] = 0
    indices[50:70, 70:90] = 2
    decoded = []
    for i, (crs, transform) in enumerate((('EPSG:26713', TRANSFORM),
                                         ('EPSG:4326', Affine(.01, 0, 10, 0, -.01, 50)))):
        path = tmp_path/f'palette-{i}.tif'
        with rasterio.open(path, 'w', driver='GTiff', width=100, height=80, count=1,
                           dtype='uint8', crs=crs, transform=transform, nodata=0) as dst:
            dst.write(indices, 1)
            dst.write_colormap(1, {0: (0, 0, 0, 0), 1: (235, 180, 120, 255), 2: (60, 180, 240, 255)})
        raster = read_raster(path, max_side=50)
        assert raster.transform == transform * Affine.scale(2)
        decoded.append(raster)
    np.testing.assert_array_equal(decoded[0].rgb, decoded[1].rgb)
    np.testing.assert_array_equal(decoded[0].valid, decoded[1].valid)
