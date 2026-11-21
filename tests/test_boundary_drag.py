import numpy as np
import pytest
from shapely import STRtree
from shapely.geometry import Polygon, box

from geotrace.boundary_drag import EdgeIndex, apply_drag
from geotrace.editing import transform_geometry, _result_from_features
from test_editing import baseline, assert_coverage


def partition(shapes):
    result=baseline()
    features=[]
    for i,polygon in enumerate(shapes,1):
        features.append(dict(geometry=transform_geometry(polygon,result.raster.transform),
                             properties=dict(result.features[0]['properties'],object_id=i)))
    return _result_from_features(result.raster,features,result.colors,result.fields)


def plan(result,x,y,tolerance=2,single=False):
    return EdgeIndex(result).plan(x,y,tolerance,single,STRtree([f['geometry'] for f in result.features]))


def test_t_junction_and_extra_collinear_vertex():
    # The left side has one long edge; two right neighbours meet it at y=30.
    right=Polygon([(50,3),(94,3),(94,30),(50,30),(50,16)])
    result=partition([box(32,3,50,57),right,box(50,30,94,57)])
    assert plan(result,43,12) is None
    with pytest.raises(ValueError,match='Стык'):
        plan(result,50,30)
    drag=plan(result,49.5,25,4)
    assert drag.pixels[:,1].max()<=30+1e-7
    edited=apply_drag(result,drag,-3,.2)
    assert_coverage(result,edited)
    assert result.features[2]==edited.features[2]
    assert np.max(drag.weights)==pytest.approx(1)
    assert drag.weights[[0,-1]].tolist()==[0,0]
    point=plan(result,50.6,15.7,2,True)
    assert np.count_nonzero(point.weights)==1
    assert point.pixels[point.weights.argmax()]==pytest.approx([50,16])
    assert_coverage(result,apply_drag(result,point,-2,-.1))


def test_closed_hole_shared_with_island_and_invalid_drop():
    shell=box(32,3,94,57)
    island=box(55,17,75,34)
    result=partition([shell.difference(island),island])
    drag=plan(result,55.1,17.1,2)
    assert len(drag.owners)==2
    assert_coverage(result,apply_drag(result,drag,-1.5,-.5))
    with pytest.raises(ValueError):
        apply_drag(result,drag,100,60)
    assert result.features[1]['properties']['object_id']==2


def test_outer_edge_checks_nodata_and_disconnected_neighbour():
    result=partition([box(31,12,46,35),box(50,12,65,35)])
    with pytest.raises(ValueError,match='данных'):
        apply_drag(result,plan(result,31,25),-7,0)
    with pytest.raises(ValueError,match='соседний'):
        apply_drag(result,plan(result,46,21),7,0)
    edited=apply_drag(result,plan(result,46,21),2,0)
    assert edited.features[1]==result.features[1]
    assert edited.features[0]['properties']['unit_name']==result.features[0]['properties']['unit_name']
