from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from PIL import Image
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from geotrace.core import segment, read_raster, Result, vectorize, save_preview
from geotrace.editing import transform_geometry, draw_polygon
from geotrace.region import read_kml, set_area, clip_result, footprint, save_area_preview
from geotrace.project import save_project, load_project
from geotrace.quality import analysis_raster, prepare_job, quality_job
from test_core import sample


def kml(path, geometries):
    def ring(tag, coords):
        return f'<{tag}><LinearRing><coordinates>' + ' '.join(f'{x},{y},0' for x, y in coords) + f'</coordinates></LinearRing></{tag}>'
    marks = []
    for i, geometry in enumerate(geometries):
        body = ring('outerBoundaryIs', geometry.exterior.coords)
        body += ''.join(ring('innerBoundaryIs', r.coords) for r in geometry.interiors)
        marks.append(f'<Placemark><name>Участок {i+1}</name><Polygon>{body}</Polygon></Placemark>')
    path.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'+''.join(marks)+'</Document></kml>', encoding='utf8')
    return path


def test_kml_holes_multiple_contours_projection_and_exact_crop(tmp_path):
    from rasterio.warp import transform_geom
    from shapely.geometry import shape, mapping
    original = sample()
    projected = replace(original, crs_wkt=rasterio.crs.CRS.from_epsg(3857).to_wkt(), crs_label='EPSG:3857',
                        transform=Affine(50, 3, 6230000, 2, -50, 6800000))
    polygon = transform_geometry(Polygon([(5.2, 5.7), (85.1, 8.8), (73.3, 53.1), (5.2, 5.7)],
                                         [[(48, 23), (54, 23), (53, 29), (48, 23)]]), projected.transform)
    other = transform_geometry(box(88.3, 12.1, 97.6, 40.6), projected.transform)
    ll = [shape(transform_geom(projected.crs_wkt, 'EPSG:4326', mapping(g))) for g in [polygon, other]]
    records = read_kml(kml(tmp_path/'граница.kml', ll), projected)
    assert len(records) == 3
    assert records[1][1].symmetric_difference(polygon).area < 1e-3
    assert len(records[1][1].interiors) == 1
    cropped = set_area(projected, records[0][1], records[0][0])
    assert np.all(cropped.valid <= original.valid)
    assert cropped.valid.sum() < original.valid.sum()
    old = segment(projected, 2, 10)
    old.features[0]['properties'].update(note='Проверено', review='edited')
    output = clip_result(old, cropped)
    union = unary_union([f['geometry'] for f in output.features])
    expected = footprint(cropped)
    assert union.symmetric_difference(expected).area < 1e-5
    assert sum(f['geometry'].area for f in output.features)-union.area < 1e-5
    assert len({f['properties']['object_id'] for f in output.features}) == len(output.features)
    assert all(f['properties']['note']=='Проверено' for f in output.features
               if f['properties']['class_id']==old.features[0]['properties']['class_id'])
    restored = set_area(cropped, None)
    np.testing.assert_array_equal(restored.valid, original.valid)
    assert restored.area is None and restored.source_valid is None
    with pytest.raises(ValueError):
        draw_polygon(cropped, None, [(1, 1), (2, 1), (2, 2)])


def test_area_and_layers_survive_portable_project_roundtrip(tmp_path):
    raster = sample()
    cropped = set_area(raster, transform_geometry(box(7.3, 7.8, 87.2, 50.5), raster.transform), 'Участок')
    result = segment(cropped, 2, 10)
    path = tmp_path/'область.geotrace'
    save_project(path, cropped, result, view=dict(raster_opacity=.4, polygons_visible=False))
    saved = load_project(path)
    assert saved.raster.area.equals(cropped.area)
    np.testing.assert_array_equal(saved.raster.source_valid, raster.valid)
    np.testing.assert_array_equal(saved.raster.valid, cropped.valid)
    assert saved.raster.area_name == 'Участок'
    assert saved.view['raster_opacity'] == .4 and not saved.view['polygons_visible']
    assert unary_union([f['geometry'] for f in saved.result.features]).difference(cropped.area).area < 1e-12


def test_crop_removes_image_outside_contour_and_holes_after_reopen(tmp_path):
    raster = sample()
    source_rgb = raster.rgb.copy()
    geometry = transform_geometry(Polygon([(10, 10), (90, 10), (90, 50), (10, 50)],
                                           [[(40, 20), (60, 20), (60, 40), (40, 40)]]), raster.transform)
    cropped = set_area(raster, geometry, 'Вырез')
    project = tmp_path/'crop.geotrace'
    save_project(project, cropped)
    reopened = load_project(project).raster
    for candidate in [cropped, reopened]:
        save_preview(candidate, tmp_path/'map.png')
        image = np.asarray(Image.open(tmp_path/'map.png'))
        assert not image[~candidate.valid].any()
        assert not image[30, 50].any()  # KML hole
        assert not image[3, 3].any()  # Outside outer ring
        np.testing.assert_array_equal(image[candidate.valid, :3], source_rgb[candidate.valid])
        assert image[candidate.valid, 3].min() == 255
        save_area_preview(candidate, candidate.area, tmp_path/'outline.png')
        outline = np.asarray(Image.open(tmp_path/'outline.png'))
        assert not outline[3, 3].any() and not outline[30, 50].any()
    restored = set_area(reopened, None)
    save_preview(restored, tmp_path/'restored.png')
    restored_image = np.asarray(Image.open(tmp_path/'restored.png'))
    np.testing.assert_array_equal(restored_image[:, :, 3], raster.valid.astype('uint8')*255)
    np.testing.assert_array_equal(raster.rgb, source_rgb)


def test_quality_worker_clips_to_exact_area_and_preserves_manual_work(tmp_path):
    raster = sample()
    cropped = set_area(raster, transform_geometry(box(6.3, 6.7, 92.4, 52.6), raster.transform), 'Область')
    prepared = prepare_job(cropped, 'token', str(tmp_path))
    groups = list(range(1, len(prepared['palette'])+1))
    result, _ = quality_job(cropped, None, 'token', groups, str(tmp_path))
    union = unary_union([f['geometry'] for f in result.features])
    assert union.symmetric_difference(footprint(cropped)).area < 1e-10
    result.features[0]['properties']['review'] = 'edited'
    geometry = result.features[0]['geometry']
    second, _ = quality_job(cropped, result, 'token', groups, str(tmp_path))
    assert next(f for f in second.features if f['properties']['object_id']==result.features[0]['properties']['object_id'])['geometry'].equals_exact(geometry, 0)


def test_cropped_native_analysis_reads_matching_full_source_and_clips_exactly(tmp_path):
    original = sample()
    rgb = np.repeat(np.repeat(original.rgb, 18, axis=0), 18, axis=1)
    valid = np.repeat(np.repeat(original.valid, 18, axis=0), 18, axis=1)
    path = tmp_path/'original.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=1800, height=1080, count=4,
                       dtype='uint8', crs=original.crs_wkt, transform=original.transform*Affine.scale(1/18)) as dst:
        dst.write(np.moveaxis(rgb, -1, 0), indexes=[1, 2, 3])
        dst.write(valid.astype('uint8')*255, 4)
        dst.colorinterp = (rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha)
    working = read_raster(path)
    cropped = set_area(working, transform_geometry(box(5.3, 5.7, 91.2, 51.6), original.transform), 'Контур')
    native, note = analysis_raster(cropped)
    assert native.rgb.shape[:2] == (1080, 1800) and note == 'Исходный TIFF'
    assert native.area.equals_exact(cropped.area, 0)
    labels = native.valid.astype('uint8')
    generated = Result(native, labels, vectorize(labels, native), [[80, 150, 180]])
    from geotrace.quality import to_working
    result = to_working(generated, cropped)
    assert unary_union([f['geometry'] for f in result.features]).symmetric_difference(footprint(cropped)).area < 1e-10
    path.unlink()
    fallback, note = analysis_raster(cropped)
    assert fallback is cropped and 'недоступен' in note


@pytest.mark.parametrize('content', ['<kml>', '<kml><Placemark><Point><coordinates>56,52</coordinates></Point></Placemark></kml>',
                                  '<kml><Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>nan,1 2,3 4,5</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>'])
def test_invalid_kml_leaves_original_raster_untouched(tmp_path, content):
    raster = sample()
    path = tmp_path/'bad.kml'
    path.write_text(content)
    before = raster.valid.copy()
    with pytest.raises(ValueError):
        read_kml(path, raster)
    np.testing.assert_array_equal(raster.valid, before)
    with pytest.raises(ValueError):
        set_area(raster, box(0, 0, 1, 1))
