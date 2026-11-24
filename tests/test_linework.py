import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS
from shapely.geometry import LineString

from geotrace.core import Raster
from geotrace.linework import _paths, detect_lines, join_lines, edit_vertex, drag_line, split_line


def test_trace_keeps_branches_and_closed_loops_without_duplicate_edges():
    mask=np.zeros((40,60),bool)
    mask[5:25,10]=True;mask[15,10:25]=True
    mask[5:16,40]=True;mask[5:16,50]=True;mask[5,40:51]=True;mask[15,40:51]=True
    paths=list(_paths(mask))
    edges=[tuple(sorted((a,b))) for p in paths for a,b in zip(p,p[1:])]
    assert len(edges)==len(set(edges))
    assert sum(p[0]==p[-1] for p in paths)==1
    assert sum((10,15) in (p[0],p[-1]) for p in paths)==3


def test_color_trace_ignores_broad_fill_and_remains_a_review_proposal():
    rgb=np.full((120,180,3),255,np.uint8)
    rgb[30:33,10:150]=[181,50,57]
    rgb[60:105,30:110]=[181,50,57]
    raster=Raster('',rgb,np.ones((120,180),bool),Affine(3,0,1e6,0,-3,7e6),CRS.from_epsg(3857).to_wkt(),'EPSG:3857',(180,120))
    found=detect_lines(raster,[dict(id=1,name='Разлом',colors=['#b53239'],style={})])
    assert len(found)==1
    assert found[0]['geometry'].length>350
    assert found[0]['properties']['review']=='unreviewed'


def test_join_uses_both_ends_and_vertex_edits_preserve_valid_lines():
    joined=join_lines([LineString([(10,0),(20,0)]),LineString([(0,0),(10,0)]),LineString([(30,0),(20,0)])])
    assert joined.length==30 and joined.is_simple
    inserted=edit_vertex(joined,(15,0))
    assert (15.,0.) in inserted.coords
    bent=drag_line(inserted,(15,0),0,2,8)
    assert max(p[1] for p in bent.coords)==2
    parts=split_line(bent,(15,2))
    assert sum(p.length for p in parts)==pytest.approx(bent.length)
    assert len(edit_vertex(inserted,(15,0),remove=True).coords)==len(joined.coords)
    with pytest.raises(ValueError):
        edit_vertex(LineString([(0,0),(1,0)]),(0,0),remove=True)
