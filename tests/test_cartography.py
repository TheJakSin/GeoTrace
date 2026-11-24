from copy import deepcopy
import re
import xml.etree.ElementTree as ET

import fiona
import pytest
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from geotrace.cartography import layout_defaults, layout_svg, validate_layout, write_layout
from geotrace.workspace_app import render_application
from geotrace.workspace_export import export_workspace
from geotrace.spatial import measurements
from test_document import two_sheets


def test_legend_preserves_long_names_and_movable_elements(tmp_path):
    doc=two_sheets();result=doc.compose(doc.canvas(200))
    options=layout_defaults(doc)
    options.update(raster=False,title='Карта <участка>',dpi=72)
    options['legend_names']={str(c['id']):('Условное обозначение '+str(c['id'])+' ')*8 for c in doc.classes}
    options['positions']['legend']=[.76,.12,.20,.15]
    pages=layout_svg(doc,result,options)
    assert len(pages)>1
    combined=''.join(''.join(ET.fromstring(page).itertext()) for page in pages)
    assert 'Карта <участка>' in combined
    for value in options['legend_names'].values():
        assert value in combined
    invalid=deepcopy(options);invalid['positions']['map'][0]=.99
    with pytest.raises(ValueError):validate_layout(invalid,doc)
    render_application()
    output=tmp_path/'map.pdf';write_layout(doc,result,options,output)
    data=output.read_bytes()
    assert data.startswith(b'%PDF')
    assert len(re.findall(rb'/Type /Page\b',data))==len(pages)
    with pytest.raises(FileExistsError):write_layout(doc,result,options,output)


def test_geopackage_keeps_clipped_line_as_one_measured_object(tmp_path):
    doc=two_sheets();canvas=doc.canvas(200)
    x0,y0,x1,y1=canvas.area.bounds
    doc.areas=[dict(name='Два контура',geometry=unary_union([box(x0,y0,x0+(x1-x0)*.3,y1),box(x0+(x1-x0)*.7,y0,x1,y1)]))]
    doc.active_area=0
    doc.line_types=[dict(id=1,name='Разлом',colors=['#b53239'],style={})]
    line=LineString([(x0,(y0+y1)/2),(x1,(y0+y1)/2)])
    doc.lines=[dict(geometry=line,properties=dict(object_id=7,type_id=1,name='F7',note='',origin='manual',review='edited'))]
    result=doc.compose(doc.canvas(200));path=tmp_path/'area.gpkg'
    export_workspace(doc,result,path)
    assert set(fiona.listlayers(path))=={'polygons','lines','licence_area'}
    with fiona.open(path,layer='lines') as source:
        assert len(source)==1
        record=next(iter(source))
        assert record['geometry']['type']=='MultiLineString'
        assert record['properties']['object_id']==7
        assert record['properties']['length_m']==pytest.approx(measurements(line.intersection(doc.area))['length_m'])
    with fiona.open(path,layer='polygons') as source:
        assert all(f['properties']['area_m2']>0 for f in source)


def test_atlas_omits_duplicate_union_contour_and_exports_each_area(tmp_path):
    doc=two_sheets();canvas=doc.canvas(200)
    x0,y0,x1,y1=canvas.area.bounds;xm=(x0+x1)/2
    parts=[box(x0,y0,xm,y1),box(xm,y0,x1,y1)]
    doc.areas=[dict(name='Все',geometry=unary_union(parts)),dict(name='Восток',geometry=parts[0]),dict(name='Запад',geometry=parts[1])]
    doc.active_area=0;result=doc.compose(canvas)
    settings=layout_defaults(doc);settings.update(raster=False,legend=False,dpi=72)
    render_application();path=tmp_path/'atlas.pdf';write_layout(doc,result,settings,path,atlas=True)
    assert len(re.findall(rb'/Type /Page\b',path.read_bytes()))==2
