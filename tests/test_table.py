import pytest
from geotrace.table import PolygonTable,plan_bulk
from geotrace.attributes import change_schema
from geotrace.core import segment
from test_core import sample


def data():
    result=change_schema(segment(sample(),2,10),[
        dict(key='age',label='Возраст',type='number',required=True,default=None),
        dict(key='checked',label='Проверено',type='boolean',default=None)])
    a,b=result.features
    a['properties'].update(unit_name='Песчаник',age=12,checked=False)
    b['properties'].update(unit_name='Известняк',age=2,checked=True)
    return result


def test_typed_sort_filter_and_stable_selection():
    result=data();table=PolygonTable();table.set_result(result)
    a,b=result.features
    table.sort_by('age')
    assert [result.features[i]['properties']['age'] for i in table.rows]==[2,12]
    table.choose(b['properties']['object_id'])
    table.sort_by('age')
    assert table.selected_ids=={b['properties']['object_id']}
    table.filter('ПЕСЧАНИК','all',False)
    assert table.rows==[0] and not table.selected_ids
    table.choose_filtered(True)
    assert table.selected_ids=={a['properties']['object_id']}
    table.filter('','all',True)
    assert not table.rows and not table.selected_ids
    b['properties']['age']=None;table.refresh()
    assert table.rows==[1]
    table.set_result(None)
    assert table.rowCount()==0 and table.columnCount()==6


def test_bulk_is_staged_and_only_empty_does_not_treat_zero_or_false_as_missing():
    result=data();ids=[f['properties']['object_id'] for f in result.features]
    field,value,changes=plan_bulk(result,ids,'checked',True,only_empty=True)
    assert not changes and result.features[0]['properties']['checked'] is False
    result.features[0]['properties']['age']=0
    result.features[1]['properties']['age']=None
    _,value,changes=plan_bulk(result,ids,'age','3,5',True)
    assert value==3.5 and set(changes)=={ids[1]}
    assert result.features[1]['properties']['age'] is None
    assert changes[ids[1]][1]['review']=='edited'
    _,_,cleared=plan_bulk(result,ids,'age',None,False)
    assert set(cleared)=={ids[0]}


def test_bulk_rejects_invalid_values_stale_ids_and_system_fields():
    result=data();oid=result.features[0]['properties']['object_id']
    for key,value,ids in [('age','не число',[oid]),('object_id',99,[oid]),('age',1,[99999]),('age',1,[])]:
        with pytest.raises(ValueError):plan_bulk(result,ids,key,value)
    assert result.features[0]['properties']['age']==12
