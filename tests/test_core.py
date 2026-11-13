import fiona
import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import unary_union

from geotrace.core import Raster, Result, export_gpkg, read_raster, segment, vectorize


def sample():
    h, w = 60, 100
    rgb = np.empty((h, w, 3), dtype=np.uint8)
    rgb[:, :50] = [230, 180, 80]
    rgb[:, 50:] = [110, 180, 210]
    valid = np.ones((h, w), dtype=bool)
    valid[20:30, 20:30] = False
    return Raster('synthetic', rgb, valid, Affine(0.01, 0.001, 56, 0.002, -0.01, 52),
                  rasterio.crs.CRS.from_epsg(4326).to_wkt(), 'EPSG:4326', (w, h))


def test_polygons_cover_valid_pixels_without_overlap_and_keep_hole():
    raster = sample()
    labels = np.where(np.indices(raster.valid.shape)[1] < 50, 1, 2).astype('uint8')
    labels[~raster.valid] = 0
    features = vectorize(labels, raster)
    polygons = [f['geometry'] for f in features]
    assert all(p.is_valid for p in polygons)
    union = unary_union(polygons)
    assert sum(p.area for p in polygons) == pytest.approx(union.area)
    assert union.area == pytest.approx(raster.valid.sum() * abs(raster.transform.determinant))
    reconstructed = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                             out_shape=labels.shape, transform=raster.transform)
    np.testing.assert_array_equal(reconstructed, labels)


def test_geopackage_roundtrip_crs_geometry_and_cyrillic(tmp_path):
    raster = sample()
    result = segment(raster, 2, 10)
    assert len(result.features) == 2
    result.features[0]['properties']['unit_name'] = 'Чертовская свита'
    output = tmp_path / 'результат.gpkg'
    export_gpkg(result, output)
    with fiona.open(output) as src:
        assert src.crs.to_epsg() == 4326
        records = list(src)
    assert len(records) == len(result.features)
    assert records[0]['properties']['unit_name'] == 'Чертовская свита'
    for record, original in zip(records, result.features):
        assert shape(record['geometry']).equals_exact(original['geometry'], 1e-12)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        export_gpkg(result, output)
    assert before == output.read_bytes()


def test_downsampling_preserves_rotated_georeference(tmp_path):
    raster = sample()
    path = tmp_path / 'source.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=100, height=60, count=4,
                       dtype='uint8', crs='EPSG:4326', transform=raster.transform) as dst:
        dst.write(np.moveaxis(raster.rgb, -1, 0), indexes=[1, 2, 3])
        dst.write(raster.valid.astype('uint8') * 255, 4)
        dst.colorinterp = (rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha)
    smaller = read_raster(path, max_side=50)
    assert smaller.rgb.shape == (30, 50, 3)
    assert smaller.transform * (50, 30) == pytest.approx(raster.transform * (100, 60))
    assert (~smaller.valid).sum() == 25


def test_missing_crs_is_rejected(tmp_path):
    path = tmp_path / 'no-crs.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=10, height=10, count=3,
                       dtype='uint8', transform=Affine.translation(1, 1)) as dst:
        dst.write(np.full((3, 10, 10), 150, dtype='uint8'))
    with pytest.raises(ValueError, match='системой координат'):
        read_raster(path)
