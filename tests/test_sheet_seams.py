import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS
from shapely.geometry import box,LineString

from geotrace.core import Raster,Result
from geotrace.document import Document,Sheet,save_document,load_document
from geotrace.cartography import map_image
from geotrace.display import polygon_paths
from geotrace.sheet_seams import technical_boundaries,contacts
from geotrace.styles import validate_style


def maps(gap=0,affine=None):
    transform=affine or Affine(1,0,1000,0,-1,1040)
    a=np.full((40,80,3),[150,210,120],np.uint8);a[-1]=255
    b=np.full((40,80,3),[230,160,180],np.uint8)
    valid=np.ones((40,80),bool);crs=CRS.from_epsg(3857).to_wkt()
    sa=Sheet('a','Север',Raster('',a,valid.copy(),transform,crs,'EPSG:3857',(80,40)))
    sb=Sheet('b','Юг',Raster('',b,valid.copy(),transform*Affine.translation(0,40+gap),crs,'EPSG:3857',(80,40)))
    doc=Document(sheets=[sa,sb],active_sheet='a',classes=[dict(id=1,name='A',style=validate_style({})),dict(id=2,name='B',style=validate_style({}))])
    return doc


def test_scan_edge_is_display_only_and_bounded_in_native_pixels():
    doc=maps();a,b=[s.raster.rgb.copy() for s in doc.sheets]
    bounds=(1010,990,1070,1010)
    doc.settings['smooth_scan_seams']=False;raw=np.array(map_image(doc,bounds,600,200))
    doc.settings['smooth_scan_seams']=True;fixed=np.array(map_image(doc,bounds,600,200))
    assert np.all(fixed[:,:,3]==raw[:,:,3])
    assert raw[95,:,:3].mean()>240 and fixed[95,:,:3].mean()<210
    # The band is at most two source pixels on either side, not 2 screen pixels.
    assert np.array_equal(raw[:80],fixed[:80]) and np.array_equal(raw[120:],fixed[120:])
    assert np.array_equal(a,doc.sheets[0].raster.rgb) and np.array_equal(b,doc.sheets[1].raster.rgb)
    single=np.array(map_image(doc,bounds,600,200,only_sheet='a'))
    assert np.all(single[95,:,:3]==raw[95,:,:3])
    # Sampling uses source/world coordinates, so a viewport crop cannot change it.
    crop=np.array(map_image(doc,(1020,995,1060,1005),400,100))
    assert np.array_equal(crop,fixed[50:150,100:500])


@pytest.mark.parametrize('gap',[1,-2])
def test_true_gap_or_overlap_is_not_smoothed(gap):
    doc=maps(gap);assert contacts(doc)[1]==()
    doc.settings['smooth_scan_seams']=False;a=np.array(map_image(doc,(1000,958,1080,1040),160,164))
    doc.settings['smooth_scan_seams']=True;b=np.array(map_image(doc,(1000,958,1080,1040),160,164))
    assert np.array_equal(a,b)


def test_hidden_sheet_and_kml_holes_are_never_painted_over():
    doc=maps();doc.sheets[1].visible=False
    doc.settings['smooth_scan_seams']=False;a=np.array(map_image(doc,(1010,990,1070,1010),600,200))
    doc.settings['smooth_scan_seams']=True;b=np.array(map_image(doc,(1010,990,1070,1010),600,200))
    assert np.array_equal(a,b)
    doc.sheets[1].visible=True
    doc.areas=[dict(name='С отверстием',geometry=box(1000,960,1080,1040).difference(box(1030,995,1050,1005)))];doc.active_area=0
    image=np.array(map_image(doc,(1010,990,1070,1010),600,200))
    assert not image[50:150,200:400,3].any()


def polygons(doc):
    raster=doc.canvas(80)
    features=[dict(geometry=s.footprint(),properties=dict(object_id=i+1,class_id=i+1,source_sheet=s.id,source_group=1,origin='automatic',review='new',note='')) for i,s in enumerate(doc.sheets)]
    return Result(raster,np.zeros(raster.valid.shape,np.uint16),features,[[150,210,120],[230,160,180]])


def test_only_matching_class_on_shared_frame_loses_technical_outline():
    doc=maps();result=polygons(doc)
    assert technical_boundaries(doc,result)=={}
    result.features[1]['properties']['class_id']=1
    cuts=technical_boundaries(doc,result)
    assert set(cuts)=={0,1} and all(c.equals(LineString([(1000,1000),(1080,1000)])) for c in cuts.values())
    paths=polygon_paths(result,doc,(0,0,80,80),10)
    seam=LineString([(0,40),(80,40)])
    assert sum(LineString(row['points']).intersection(seam).length for row in paths)<1e-10
    selected=polygon_paths(result,doc,(0,0,80,80),10,selected=[1])
    assert sum(LineString(row['points']).intersection(seam).length for row in selected if row['selected'])==pytest.approx(80)
    # A true contact away from the frame remains, even within one class.
    result.features[0]['geometry']=box(1000,1002,1080,1040)
    result.features[1]['geometry']=box(1000,960,1080,1002)
    assert technical_boundaries(doc,result)=={}


def test_rotated_seam_and_setting_roundtrip_preserve_geometry(tmp_path):
    doc=maps(affine=Affine.translation(100,100)*Affine.rotation(27)*Affine.scale(1,-1))
    domain=doc.sheets[0].footprint().union(doc.sheets[1].footprint())
    bounds=domain.bounds;doc.settings['smooth_scan_seams']=False
    raw=np.array(map_image(doc,bounds,400,400));doc.settings['smooth_scan_seams']=True
    fixed=np.array(map_image(doc,bounds,400,400))
    assert np.array_equal(raw[:,:,3],fixed[:,:,3]) and not np.array_equal(raw,fixed)
    doc.settings['smooth_scan_seams']=False
    path=tmp_path/'seams.geotrace';r=doc.canvas(100);save_document(path,doc,r,None)
    restored=load_document(path)
    assert restored.settings['smooth_scan_seams'] is False
    for before,after in zip(doc.sheets,restored.sheets):
        assert before.raster.transform==after.raster.transform
        assert np.array_equal(before.raster.rgb,after.raster.rgb)


def test_yandex_outlines_omit_only_matched_cut_without_changing_polygons():
    from geotrace.webmap import vector_context
    doc=maps();result=polygons(doc)
    before=[f['geometry'].wkb for f in result.features]
    assert len(vector_context(result,doc)['features'])==2
    result.features[1]['properties']['class_id']=1
    rows=vector_context(result,doc)['features']
    polygons_out=[f for f in rows if f['properties']['kind']=='polygon']
    outlines=[f for f in rows if f['properties']['kind']=='polygon_outline']
    assert len(polygons_out)==2 and outlines
    assert all(f['options']['strokeWidth']==0 for f in polygons_out)
    assert all(f['geometry']['type']=='LineString' for f in outlines)
    assert [f['geometry'].wkb for f in result.features]==before


def test_clean_contact_is_not_blurred():
    doc=maps();doc.sheets[0].raster.rgb[-1]=[150,210,120]
    bounds=(1010,990,1070,1010)
    doc.settings['smooth_scan_seams']=False;raw=np.array(map_image(doc,bounds,600,200))
    doc.settings['smooth_scan_seams']=True;fixed=np.array(map_image(doc,bounds,600,200))
    assert np.array_equal(raw,fixed)


def test_different_scan_resolution_limits_each_side_to_its_own_pixels():
    doc=maps()
    # The southern source has four times finer native pixels; display blending
    # may not spread two coarse pixels into eight fine pixels there.
    doc.sheets[1].raster.source_size=(320,160)
    bounds=(1010,990,1070,1010)
    doc.settings['smooth_scan_seams']=False;raw=np.array(map_image(doc,bounds,600,200))
    doc.settings['smooth_scan_seams']=True;fixed=np.array(map_image(doc,bounds,600,200))
    assert not np.array_equal(raw,fixed)
    assert np.array_equal(raw[:80],fixed[:80])
    assert np.array_equal(raw[105:],fixed[105:])
