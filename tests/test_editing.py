import numpy as np
import pytest
from shapely.geometry import LineString
from shapely.ops import unary_union

from geotrace.core import segment, export_gpkg
from geotrace.editing import edit_geometry, transform_geometry
from geotrace.project import save_project, load_project
from test_core import sample


def baseline():
    result = segment(sample(), 2, 10)
    # Stable left-to-right order regardless of color clustering IDs.
    result.features.sort(key=lambda f: transform_geometry(f['geometry'], ~result.raster.transform).centroid.x)
    result.features[0]['properties'].update(unit_index='А', unit_name='Первая свита', review='edited')
    result.features[1]['properties'].update(unit_index='Б', unit_name='Вторая свита', review='accepted')
    return result


def assert_coverage(original, edited):
    before = unary_union([f['geometry'] for f in original.features])
    geometries = [f['geometry'] for f in edited.features]
    after = unary_union(geometries)
    assert all(g.is_valid for g in geometries)
    assert before.symmetric_difference(after).area < 1e-10
    assert abs(sum(g.area for g in geometries) - after.area) < 1e-10
    assert len({f['properties']['object_id'] for f in edited.features}) == len(edited.features)
    assert (edited.labels[~edited.raster.valid] == 0).all()


def test_merge_preserves_hole_and_first_attributes_and_does_not_mutate_source():
    original = baseline()
    ids = [f['properties']['object_id'] for f in original.features]
    edited = edit_geometry(original, ids, 'merge')
    assert_coverage(original, edited)
    assert len(edited.features) == 1
    assert len(edited.features[0]['geometry'].interiors) == 1
    assert edited.features[0]['properties']['unit_index'] == 'А'
    assert edited.features[0]['properties']['object_id'] == ids[0]
    assert original.features[1]['properties']['unit_index'] == 'Б'
    assert len(original.features) == 2


def test_split_oblique_line_preserves_attributes_neighbors_and_project_export(tmp_path):
    original = baseline()
    first, other = original.features
    edited = edit_geometry(original, [first['properties']['object_id']], 'split', [(-2,40), (52,50)])
    assert_coverage(original, edited)
    assert len(edited.features) == 3
    unchanged = next(f for f in edited.features if f['properties']['object_id'] == other['properties']['object_id'])
    assert unchanged['geometry'].equals_exact(other['geometry'], 0)
    assert unchanged['properties'] == other['properties']
    parts = [f for f in edited.features if f['properties']['origin'] == 'manual_split']
    assert len(parts) == 2 and all(f['properties']['unit_name'] == 'Первая свита' for f in parts)
    path = tmp_path / 'Правка.geotrace'
    save_project(path, edited.raster, edited)
    loaded = load_project(path).result
    assert_coverage(edited, loaded)
    export_gpkg(loaded, tmp_path / 'Правка.gpkg')
    import fiona
    from shapely.geometry import shape
    with fiona.open(tmp_path / 'Правка.gpkg') as src:
        records = list(src)
    assert len(records) == 3
    for record, feature in zip(records, loaded.features):
        assert shape(record['geometry']).equals_exact(feature['geometry'], 1e-12)


def test_boundary_moves_both_sides_without_gaps_and_keeps_identity():
    original = baseline()
    ids = [f['properties']['object_id'] for f in original.features]
    edited = edit_geometry(original, ids, 'boundary', [(50,0), (65.25,30), (50,60)])
    assert_coverage(original, edited)
    by_id = {f['properties']['object_id']: f for f in edited.features}
    for old in original.features:
        new = by_id[old['properties']['object_id']]
        assert new['properties']['unit_name'] == old['properties']['unit_name']
        assert not new['geometry'].equals(old['geometry'])
    shared = edited.features[0]['geometry'].boundary.intersection(edited.features[1]['geometry'].boundary)
    pixel_shared = transform_geometry(shared, ~original.raster.transform)
    assert pixel_shared.distance(LineString([(50,0),(65.25,30),(50,60)])) < 1e-7


@pytest.mark.parametrize('points', [[(5,5),(15,5)], [(0,10),(45,50),(0,50),(45,10)], [(5,5),(5,5)]])
def test_invalid_cut_is_rejected_without_changing_input(points):
    original = baseline()
    before = [f['geometry'].wkb for f in original.features]
    with pytest.raises(ValueError):
        edit_geometry(original, [original.features[0]['properties']['object_id']], 'split', points)
    assert before == [f['geometry'].wkb for f in original.features]


def test_prompt_cannot_overwrite_subpixel_manual_boundary():
    from geotrace.core import apply_proposal
    from geotrace.prompts import Proposal
    original = baseline()
    ids = [f['properties']['object_id'] for f in original.features]
    edited = edit_geometry(original, ids, 'boundary', [(49.25,0),(49.25,60)])
    # The center of pixel (49,10) belongs to the unprotected side, but a quarter of it
    # intersects the edited polygon. Checking rasterized object IDs alone misses this.
    for f in edited.features:
        if f['properties']['object_id'] == ids[1]:
            f['properties']['review'] = 'unreviewed'
    mask = np.zeros(edited.raster.valid.shape, bool)
    mask[10,49] = True
    with pytest.raises(ValueError, match='отредактированную'):
        apply_proposal(edited.raster, edited, Proposal(mask,(0,0,100,60),'color',False))


def test_point_contact_is_not_a_mergeable_shared_boundary():
    from shapely.geometry import box
    original = baseline()
    for f, geometry in zip(original.features, [box(0,0,10,10), box(10,10,20,20)]):
        f['geometry'] = transform_geometry(geometry, original.raster.transform)
    with pytest.raises(ValueError, match='соседних'):
        edit_geometry(original, [f['properties']['object_id'] for f in original.features], 'merge')
