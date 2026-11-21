import numpy as np
import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from geotrace.core import Result
from geotrace.editing import draw_polygon, move_vertex, transform_geometry
from geotrace.project import save_project, load_project
from test_core import sample


def objects(polygons):
    raster=sample()
    features=[]
    for i,p in enumerate(polygons):
        features.append(dict(geometry=transform_geometry(p,raster.transform),properties=dict(
            object_id=i+1,class_id=1,unit_index=str(i+1),unit_name='Свита '+str(i+1),note='',review='edited',origin='manual_draw')))
    return Result(raster,np.zeros(raster.valid.shape,dtype='uint16'),features,[[75,183,168]])


def test_draw_without_segmentation_roundtrip_and_reject_overlap(tmp_path):
    raster=sample()
    result=draw_polygon(raster,None,[(5,5),(15,5),(15,15),(5,15)])
    assert len(result.features)==1 and result.features[0]['properties']['origin']=='manual_draw'
    next_result=draw_polygon(raster,result,[(15,5),(19,5),(19,15),(15,15)])
    assert len(next_result.features)==2 and len(next_result.colors)==1
    assert next_result.features[0]['geometry'].equals_exact(result.features[0]['geometry'],0)
    with pytest.raises(ValueError,match='занято'):
        draw_polygon(raster,result,[(10,10),(18,10),(18,18),(10,18)])
    path=tmp_path/'ручной.geotrace';save_project(path,raster,next_result)
    loaded=load_project(path).result
    for a,b in zip(loaded.features,next_result.features):
        assert a['geometry'].equals_exact(b['geometry'],0) and a['properties']==b['properties']


@pytest.mark.parametrize('points', [[(15,15),(35,15),(35,35),(15,35)],
                                     [(-1,0),(5,0),(5,10)],[(5,5),(15,15),(5,15),(15,5)]])
def test_draw_rejects_nodata_outside_and_bowtie(points):
    with pytest.raises(ValueError): draw_polygon(sample(),None,points)


def assert_no_gaps_or_overlaps(before,after):
    a=unary_union([f['geometry'] for f in before.features]);b=unary_union([f['geometry'] for f in after.features])
    assert a.symmetric_difference(b).area<1e-10
    assert abs(sum(f['geometry'].area for f in after.features)-b.area)<1e-10
    assert all(f['geometry'].is_valid for f in after.features)


def test_move_common_vertex_with_omitted_collinear_node():
    original=objects([Polygon([(40,0),(50,0),(50,30),(50,60),(40,60)]), box(50,0,90,60)])
    updated=move_vertex(original,1,[(50,30),(60,30)])
    assert_no_gaps_or_overlaps(original,updated)
    assert all(f['properties']['origin']=='manual_vertex' for f in updated.features)
    assert [f['properties']['unit_name'] for f in updated.features]==['Свита 1','Свита 2']
    assert original.features[0]['properties']['origin']=='manual_draw'


def test_t_junction_moves_three_neighbors_consistently():
    original=objects([box(40,0,50,60),box(50,0,90,30),box(50,30,90,60)])
    updated=move_vertex(original,2,[(50,30),(57,35)])
    assert_no_gaps_or_overlaps(original,updated)
    assert all(f['properties']['origin']=='manual_vertex' for f in updated.features)


def test_vertex_nodes_nearest_collinear_point_before_moving():
    original=objects([Polygon([(40,0),(50,0),(50,30),(50,60),(40,60)]),
                      Polygon([(50,0),(90,0),(90,60),(50,60),(50,40),(50,30),(50,20)])])
    updated=move_vertex(original,1,[(50,30),(60,30)])
    assert_no_gaps_or_overlaps(original,updated)


def test_external_vertex_can_move_inward_but_not_overlap_other_objects_or_nodata():
    original=objects([box(40,5,60,25),box(62,5,80,25)])
    updated=move_vertex(original,1,[(60,5),(55,10)])
    assert updated.features[0]['geometry'].area<original.features[0]['geometry'].area
    assert updated.features[1]['geometry'].equals_exact(original.features[1]['geometry'],0)
    with pytest.raises(ValueError): move_vertex(original,1,[(60,5),(70,10)])
    with pytest.raises(ValueError): move_vertex(original,1,[(40,5),(25,25)])
    with pytest.raises(ValueError): move_vertex(original,1,[(60,5),(35,15)])


def test_vertex_on_shared_hole_and_island_preserves_coverage():
    outer=Polygon(box(40,5,90,55).exterior.coords,[box(55,20,70,40).exterior.coords])
    original=objects([outer,box(55,20,70,40)])
    updated=move_vertex(original,1,[(55,20),(54,19)])
    assert_no_gaps_or_overlaps(original,updated)
    assert len(updated.features[0]['geometry'].interiors)==1
