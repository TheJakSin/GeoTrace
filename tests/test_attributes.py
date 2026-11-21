import json
import zipfile
import fiona
import numpy as np
import pytest
from geotrace.attributes import change_schema,parse_value,validate_schema,values_for
from geotrace.core import segment,export_gpkg,apply_proposal
from geotrace.editing import edit_geometry,draw_polygon
from geotrace.project import save_project,load_project
from geotrace.prompts import Proposal
from test_core import sample


def field(key,kind='text',default=None,**options):
    return dict(key=key,label=key,type=kind,default=default,**options)


def test_all_types_project_and_geopackage_roundtrip(tmp_path):
    fields=[field('lithology','choice','Песчаник',choices=['Песчаник','Известняк']),
            field('thickness','number','12,5'),field('samples','integer','7'),
            field('checked','boolean','Нет'),field('observed','date','2026-11-13'),field('author','text','Женя')]
    result=change_schema(segment(sample(),2,10),fields)
    path=tmp_path/'поля.geotrace';save_project(path,result.raster,result)
    loaded=load_project(path).result
    assert loaded.fields==result.fields
    assert loaded.features[0]['properties']['checked'] is False
    export_gpkg(loaded,tmp_path/'поля.gpkg')
    with fiona.open(tmp_path/'поля.gpkg') as src:
        record=next(iter(src));schema=src.schema
    assert record['properties']['thickness']==12.5
    assert record['properties']['checked']==0
    assert record['properties']['samples']==7
    assert record['properties']['observed']=='2026-11-13'
    assert record['properties']['author']=='Женя'
    assert record['properties']['lithology']=='Песчаник'
    assert schema['properties']['observed']=='date'


def test_required_fields_block_export_but_not_draft_saving(tmp_path):
    result=change_schema(segment(sample(),2,10),[field('age',required=True)])
    save_project(tmp_path/'draft.geotrace',result.raster,result)
    assert load_project(tmp_path/'draft.geotrace').result.fields[0]['required']
    with pytest.raises(ValueError,match='Полигон №'):
        export_gpkg(result,tmp_path/'incomplete.gpkg')
    assert not (tmp_path/'incomplete.gpkg').exists()


def test_schema_conversion_and_removal_are_atomic():
    result=change_schema(segment(sample(),2,10),[field('count','text','не число')])
    with pytest.raises(ValueError,match='целое число'):
        change_schema(result,[field('count','integer')])
    assert result.features[0]['properties']['count']=='не число' and result.fields[0]['type']=='text'
    removed=change_schema(result,[])
    assert 'count' not in removed.features[0]['properties'] and result.fields
    for schema in ([field('geom')],[field('object_id')],[field('x'),field('x')],[field('choice','choice','B',choices=['A'])]):
        with pytest.raises(ValueError):validate_schema(schema)


def test_types_reject_lossy_or_invalid_values():
    for kind,value in [('integer','1.2'),('integer','9007199254740990.9'),('number','NaN'),
                       ('date','2026-02-30'),('boolean','maybe')]:
        schema=validate_schema([field('value',kind)])[0]
        with pytest.raises(ValueError):parse_value(schema,value)
    assert parse_value(validate_schema([field('value','boolean')])[0],'Да') is True


def test_custom_values_survive_geometry_and_new_objects_get_defaults():
    result=change_schema(segment(sample(),2,10),[field('age','integer',20)])
    first=result.features[0]
    first['properties']['age']=75
    # Split a horizontal line through both halves; only the chosen feature is changed.
    split=edit_geometry(result,[first['properties']['object_id']],'split',[(-1,40),(101,40)])
    assert split.fields==result.fields
    assert all(f['properties']['age']==75 for f in split.features if f['properties']['origin']=='manual_split')
    mask=np.zeros(result.raster.valid.shape,bool);mask[5:10,5:10]=True
    prompted=apply_proposal(result.raster,result,Proposal(mask,(0,0,100,60),'color',False))
    assert prompted.fields==result.fields
    assert all(f['properties']['age']==20 for f in prompted.features if f['properties']['origin']=='prompt_color')
    from geotrace.core import Result
    empty=Result(result.raster,np.zeros(result.raster.valid.shape,'uint16'),[],[],result.fields)
    drawn=draw_polygon(result.raster,empty,[(5,5),(15,5),(15,15),(5,15)])
    assert drawn.fields==result.fields and drawn.features[0]['properties']['age']==20


def test_v1_projects_remain_readable(tmp_path):
    result=segment(sample(),2,10);path=tmp_path/'old.geotrace'
    save_project(path,result.raster,result)
    with zipfile.ZipFile(path) as z: entries={n:z.read(n) for n in z.namelist()}
    meta=json.loads(entries['project.json']);meta['version']=1;meta.pop('fields')
    entries['project.json']=json.dumps(meta).encode()
    with zipfile.ZipFile(path,'w') as z:
        for name,data in entries.items():z.writestr(name,data)
    assert load_project(path).result.fields==[]
