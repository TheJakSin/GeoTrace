import copy
import numpy as np
import pytest
from shapely.geometry import Polygon,box,LineString
from shapely.ops import unary_union
from geotrace.smoothing import smooth_partition,smooth_result


def partition():
    edge=[(20+i,10+i//2) for i in range(20)]
    left=Polygon([(0,0),(20,0),*edge,(40,40),(0,40)])
    return [left,box(0,0,60,40).difference(left)]


@pytest.mark.parametrize('mode',['gentle','smooth','simplify'])
def test_shared_smoothing_keeps_coverage_outer_edge_holes_and_area(mode):
    before=partition();after,info=smooth_partition(before,mode=mode)
    assert info['changed']==2
    assert after[0].boundary.intersection(after[1].boundary).length>0
    assert unary_union(after).equals(unary_union(before))
    assert sum(p.area for p in after)==pytest.approx(sum(p.area for p in before))
    assert all(p.is_valid for p in after)
    assert all(len(a.interiors)==len(b.interiors) for a,b in zip(before,after))
    assert max(a.boundary.hausdorff_distance(b.boundary) for a,b in zip(before,after))<=1.6


def test_small_island_hole_and_triple_junction_survive():
    before=partition();island=box(5,5,5.4,5.4)
    before=[before[0].difference(island),before[1],island]
    after,_=smooth_partition(before,mode='smooth',strength=100)
    assert len(after)==3 and after[2].area>0
    assert len(after[0].interiors)==1 and unary_union(after).equals(unary_union(before))
    tri=[box(0,0,10,20),box(10,0,20,10),box(10,10,20,20)]
    changed,_=smooth_partition(tri)
    assert all(g.boundary.distance(__import__('shapely').Point(10,10))==0 for g in changed)


def test_zero_strength_is_exact_and_source_objects_are_immutable():
    before=partition();data=[p.wkb for p in before]
    after,info=smooth_partition(before,strength=0)
    assert [p.wkb for p in after]==data and not info['changed']
    smooth_partition(before,strength=100)
    assert [p.wkb for p in before]==data
    with pytest.raises(ValueError):smooth_partition(before,mode='unknown')


def test_selection_only_changes_incident_shared_arcs_and_keeps_junctions():
    a,b=partition();far=box(100,0,120,20)
    output,info=smooth_partition([a,b,far],selected={0})
    assert info['changed']==2 and output[2] is far


def test_world_space_manual_smoothing_preserves_ids_and_custom_attributes():
    from test_draw_vertex import objects
    from geotrace.document import Document,Sheet
    from geotrace.styles import validate_style
    before=objects(partition());before.features[0]['properties']['note']='Do not replace'
    doc=Document(sheets=[Sheet('a','a',before.raster)],classes=[dict(id=1,name='A',style=validate_style({}))])
    old=copy.deepcopy([f['properties'] for f in before.features])
    after,info=smooth_result(doc,before,object_ids={1})
    assert info['changed']>0
    assert [f['properties']['object_id'] for f in after.features]==[1,2]
    assert after.features[0]['properties']['note']=='Do not replace'
    assert all(f['properties']['origin']=='manual_smooth' for f in after.features)
    assert [f['properties'] for f in before.features]==old


def test_digitization_automatically_smooths_a_pixelated_closed_contact():
    from affine import Affine
    from rasterio.crs import CRS
    from geotrace.core import Raster
    from geotrace.quality import prepare,segment_prepared
    y,x=np.ogrid[:128,:128];rgb=np.full((128,128,3),[240,220,150],np.uint8)
    rgb[(x-64)**2+(y-64)**2<30**2]=[120,180,130]
    raster=Raster('',rgb,np.ones((128,128),bool),Affine(1,0,1000,0,-1,2000),CRS.from_epsg(3857).to_wkt(),'EPSG:3857',(128,128))
    result,info=segment_prepared(prepare(raster))
    assert info['smoothing']['changed']==2
    assert info['smoothing']['max_shift_pixels']<.5
    assert unary_union([f['geometry'] for f in result.features]).area==pytest.approx(128*128)


@pytest.mark.parametrize('profile',['baseline','scan'])
def test_classic_automatic_profiles_also_smooth_without_marking_manual(tmp_path,profile):
    from affine import Affine
    from rasterio.crs import CRS
    from geotrace.core import Raster
    from geotrace.app import auto_job
    y,x=np.ogrid[:128,:128];rgb=np.full((128,128,3),[240,220,150],np.uint8)
    rgb[(x-64)**2+(y-64)**2<30**2]=[120,180,130]
    raster=Raster('',rgb,np.ones((128,128),bool),Affine(1,0,1000,0,-1,2000),CRS.from_epsg(3857).to_wkt(),'EPSG:3857',(128,128))
    result=auto_job(raster,None,2,5,profile,tmp_path)
    assert len(result.features)==2
    assert all(f['properties']['review']=='unreviewed' for f in result.features)
    pixels=[np.asarray(g['geometry'].exterior.coords)-[1000,2000] for g in result.features]
    assert any(np.max(abs(p-np.rint(p)))>.01 for p in pixels)
