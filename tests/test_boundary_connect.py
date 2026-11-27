import numpy as np
import pytest
from shapely.geometry import box,Polygon,Point
from shapely.ops import unary_union
from geotrace.boundary_connect import Context,connect_drag,connect_span,span_drag,finish,preview
from test_boundary_drag import partition,plan


def sample():return partition([box(31,10,46,45),box(50,10,70,45),box(75,10,90,45)])
def pixels(result,i):
 from geotrace.editing import transform_geometry
 return transform_geometry(result.features[i]['geometry'],~result.raster.transform)
def no_overlaps(result):
 gs=[f['geometry'] for f in result.features]
 assert sum(g.area for g in gs)==pytest.approx(unary_union(gs).area,abs=1e-7)
 assert all(g.is_valid and not g.is_empty for g in gs)


def test_drag_snaps_whole_arc_and_keeps_target_and_unrelated_geometry():
 r=sample();c=Context(r);d=plan(r,46,25,1)
 p=connect_drag(c,d,3.5,0,1);out=finish(c,p)
 assert p['snapped'] and p['target']==1
 assert pixels(out,0).boundary.intersection(pixels(out,1).boundary).length>4
 assert out.features[1]['geometry'].hausdorff_distance(r.features[1]['geometry'])<1e-10
 assert out.features[1]['properties']['object_id']==r.features[1]['properties']['object_id']
 assert out.features[2]==r.features[2]
 assert out.features[0]['properties']['object_id']==r.features[0]['properties']['object_id']
 assert out.features[0]['properties']['unit_name']==r.features[0]['properties']['unit_name']
 assert preview(c,p)['areas'];no_overlaps(out)


def test_drag_inside_neighbor_makes_new_shared_contact_without_snapping_back():
 r=sample();c=Context(r);p=connect_drag(c,plan(r,46,25,1),10,0,1)
 assert not p['snapped'];out=finish(c,p)
 assert set(p['changed'])=={0,1}
 assert pixels(out,0).boundary.intersection(pixels(out,1).boundary).length>3
 assert pixels(out,0).bounds[2]>55
 assert out.features[2]==r.features[2];no_overlaps(out)


@pytest.mark.parametrize('anchor',['source','neighbor'])
def test_two_clicks_preserve_chosen_side_and_attributes(anchor):
 r=sample();c=Context(r);d=span_drag(c,0,0,(46,17),(46,36),2)
 p=connect_span(c,d,2,anchor);out=finish(c,p)
 kept=0 if anchor=='source' else 1
 assert out.features[kept]['geometry'].hausdorff_distance(r.features[kept]['geometry'])<1e-10
 assert out.features[0]['geometry'].boundary.intersection(out.features[1]['geometry'].boundary).length>.1
 assert [f['properties']['object_id'] for f in out.features]==[f['properties']['object_id'] for f in r.features]
 assert out.features[2]==r.features[2];no_overlaps(out)


def test_target_contour_with_many_vertices_is_followed_as_one_arc():
 pts=[(50+np.sin(y/3)*.4,y) for y in range(10,46)]
 r=partition([box(31,10,46,45),Polygon([*pts,(70,45),(70,10)])]);c=Context(r)
 p=connect_span(c,span_drag(c,0,0,(46,17),(46,36),2),2)
 out=finish(c,p)
 assert out.features[1]['geometry'].hausdorff_distance(r.features[1]['geometry'])<1e-10
 assert out.features[0]['geometry'].boundary.intersection(out.features[1]['geometry'].boundary).length>.1
 no_overlaps(out)


def test_hidden_neighbor_cannot_be_overwritten_and_invalid_edits_are_rejected():
 r=sample();c=Context(r,blocked=[1]);d=plan(r,46,25,1)
 with pytest.raises(ValueError,match='скрыт'):connect_drag(c,d,10,0,1)
 with pytest.raises(ValueError,match='данных|участок|пересекает|разрезает'):connect_drag(Context(r),d,200,0,1)
 with pytest.raises(ValueError,match='той же'):span_drag(Context(r),0,0,(46,17),(70,36),2)


def test_real_hole_is_preserved_or_edit_refused():
 shell=box(50,10,70,45).difference(box(52,22,55,28))
 r=partition([box(31,10,46,45),shell]);c=Context(r);d=plan(r,46,25,1)
 with pytest.raises(ValueError,match='внутреннюю'):connect_drag(c,d,10,0,1)
 assert len(r.features[1]['geometry'].interiors)==1


def test_existing_shared_edge_still_moves_both_sides_and_preserves_coverage():
 r=partition([box(31,10,50,45),box(50,10,70,45)])
 p=connect_drag(Context(r),plan(r,50,25,1),2,0,1);out=finish(Context(r),p)
 assert set(p['changed'])=={0,1}
 assert unary_union([f['geometry'] for f in r.features]).equals(unary_union([f['geometry'] for f in out.features]))
 no_overlaps(out)
