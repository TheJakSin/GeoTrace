import numpy as np
import pytest
from shapely.geometry import box,Polygon
from shapely.ops import unary_union

from geotrace.core import Result
from geotrace.editing import draw_polygon,edit_geometry,transform_geometry
from geotrace.topology import clean_numeric_interiors,clean_result_interiors
from test_draw_vertex import objects


def test_new_inner_polygon_becomes_island_with_exact_common_boundary():
    before=objects([box(40,0,90,60)])
    before.features[0]['properties']['note']='Сохранить'
    after=draw_polygon(before.raster,before,[(55,12),(75,12),(75,35),(55,35)],1)
    shell,inner=after.features
    assert len(shell['geometry'].interiors)==1
    assert shell['properties']['note']=='Сохранить' and inner['properties']['note']==''
    assert shell['geometry'].boundary.intersection(inner['geometry'].boundary).equals(inner['geometry'].boundary)
    assert unary_union([f['geometry'] for f in after.features]).equals(before.features[0]['geometry'])


def test_new_polygon_crosses_multiple_objects_and_splits_remainders():
    before=objects([box(40,0,60,60),box(60,0,90,60)])
    after=draw_polygon(before.raster,before,[(40,20),(90,20),(90,40),(40,40)],1)
    assert len(after.features)==5
    geoms=[f['geometry'] for f in after.features];union=unary_union(geoms)
    # Overlay of rotated geographic coordinates is compared at floating-point
    # tolerance, as in the shared-boundary editing regression suite.
    assert union.symmetric_difference(unary_union([f['geometry'] for f in before.features])).area<1e-10
    assert abs(sum(g.area for g in geoms)-union.area)<1e-10
    assert len({f['properties']['object_id'] for f in after.features})==5
    assert after.features[-1]['properties']['origin']=='manual_draw'
    assert all(f['properties']['review']=='edited' for f in after.features)


def test_numeric_slits_are_removed_but_small_real_holes_and_outer_edge_remain():
    from affine import Affine
    exterior=box(3_000_000,8_000_000,3_000_100,8_000_100)
    slit=box(3_000_020,8_000_040,3_000_080,8_000_040+1e-8)
    real=box(3_000_010,8_000_010,3_000_010.001,8_000_020)
    geometry=exterior.difference(slit).difference(real)
    assert geometry.is_valid and len(geometry.interiors)==2
    cleaned=clean_numeric_interiors(geometry,Affine.identity())
    assert cleaned.is_valid and len(cleaned.interiors)==1
    assert cleaned.exterior.equals_exact(geometry.exterior,0)
    assert Polygon(cleaned.interiors[0]).equals(real)


def test_merge_removes_artificial_internal_ring_without_erasing_real_hole():
    before=objects([box(40,0,65,60),box(65,0,90,60)])
    slit=transform_geometry(box(55,35,80,35+1e-8),before.raster.transform)
    real=transform_geometry(box(45,5,48,8),before.raster.transform)
    for f in before.features:f['geometry']=f['geometry'].difference(slit).difference(real)
    after=edit_geometry(before,[1,2],'merge')
    assert len(after.features)==1 and len(after.features[0]['geometry'].interiors)==1
    assert Polygon(after.features[0]['geometry'].interiors[0]).equals(real)


def test_cleanup_removes_only_covered_unprotected_numeric_fragments():
    before=objects([box(40,0,90,60),box(50,10,70,10+1e-9),
                    box(50,20,70,20+1e-9),box(50,30,70,30.001)])
    before.features[1]['properties'].update(review='new',unit_index='',unit_name='')
    after=clean_result_interiors(before)
    assert [f['properties']['object_id'] for f in after.features]==[1,3,4]
    assert after.features[1]['properties']==before.features[2]['properties']
    assert after.features[2]['geometry'].equals_exact(before.features[3]['geometry'],0)
    # Two identical numeric fragments cannot delete each other without a real
    # covering object; isolated thin observations must survive.
    thin=objects([box(50,10,70,10+1e-9),box(50,10,70,10+1e-9)])
    for f in thin.features:f['properties'].update(review='new',unit_index='',unit_name='')
    assert len(clean_result_interiors(thin).features)==2
