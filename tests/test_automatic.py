import numpy as np
import pytest
from shapely.ops import unary_union

from geotrace.automatic import preserve_work, protected_feature
from geotrace.attributes import change_schema
from geotrace.core import segment
from geotrace.editing import move_vertex, draw_polygon
from geotrace.project import load_project, save_project
from test_core import sample


def test_scan_deterministic_and_preserves_hole_and_coverage():
    raster = sample()
    a = segment(raster, 2, 10, 'scan')
    b = segment(raster, 2, 10, 'scan')
    np.testing.assert_array_equal(a.labels, b.labels)
    assert not a.labels[~raster.valid].any()
    area = unary_union([f['geometry'] for f in a.features])
    assert sum(f['geometry'].area for f in a.features) == pytest.approx(area.area)
    assert area.area == pytest.approx(raster.valid.sum() * abs(raster.transform.determinant))
    assert all(f['properties']['origin'] == 'color_scan' for f in a.features)
    assert len(a.features) == 2
    with pytest.raises(ValueError, match='режим'):
        segment(raster, 2, 10, 'unknown')


def test_repeat_keeps_subpixel_manual_geometry_schema_and_identity(tmp_path):
    raster = sample()
    # A manually drawn island crosses the class boundary between pixel centers.
    previous = draw_polygon(raster, None, [(45.2, 35.1), (55.3, 35.2), (55.2, 42.1), (45.1, 42.2)])
    previous = change_schema(previous, [dict(key='age', label='Возраст', type='number', default=None)])
    previous.features[0]['properties'].update(unit_index='J1', age=15.5)
    before = previous.features[0]
    old_labels = previous.labels.copy()
    generated = segment(raster, 2, 10, 'scan')
    result = preserve_work(generated, previous)
    kept = next(f for f in result.features if f['properties']['object_id'] == before['properties']['object_id'])
    assert kept['properties'] == before['properties']
    assert kept['geometry'].equals_exact(before['geometry'], 0)
    assert kept['properties'] is not before['properties']
    assert result.fields == previous.fields
    new = [f for f in result.features if f is not kept]
    assert all(f['properties']['age'] is None for f in new)
    assert all(f['properties']['object_id'] > before['properties']['object_id'] for f in new)
    union = unary_union([f['geometry'] for f in result.features])
    assert sum(f['geometry'].area for f in result.features) == pytest.approx(union.area)
    assert union.symmetric_difference(unary_union([f['geometry'] for f in generated.features])).area < 1e-10
    assert all(f['geometry'].intersection(kept['geometry']).area < 1e-10 for f in new)
    np.testing.assert_array_equal(previous.labels, old_labels)
    # Repeated recomputation does not grow the palette or lose stored identities.
    again = preserve_work(generated, result)
    assert len(again.colors) == len(result.colors)
    assert again.features[0]['properties'] == kept['properties']
    path = tmp_path / 'repeat.geotrace'
    save_project(path, raster, result, [], None)
    loaded = load_project(path).result
    assert loaded.features[0]['properties'] == kept['properties']
    assert loaded.features[0]['geometry'].equals_exact(kept['geometry'], 0)


def test_attribute_protection_handles_zero_false_and_defaults():
    raster = sample()
    old = change_schema(segment(raster, 2, 10), [
        dict(key='count', label='Количество', type='integer', default=None),
        dict(key='checked', label='Проверено', type='boolean', default=False)])
    first, second = old.features
    first['properties']['count'] = 0
    assert protected_feature(first, old.fields)
    assert not protected_feature(second, old.fields)
    result = preserve_work(segment(raster, 2, 10, 'scan'), old)
    assert result.features[0]['properties'] == first['properties']
    second['properties']['note'] = 'Сохранить подпись'
    assert protected_feature(second, old.fields)
    all_kept = preserve_work(segment(raster, 2, 10), old)
    assert [f['properties'] for f in all_kept.features] == [f['properties'] for f in old.features]
    assert all(f['geometry'].equals_exact(o['geometry'],0) for f,o in zip(all_kept.features,old.features))
