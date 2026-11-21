import json
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from shapely.geometry import box
from geotrace.app import Backend
from geotrace.attributes import change_schema
from test_draw_vertex import objects


def setup(tmp_path):
    app=QGuiApplication.instance() or QGuiApplication([])
    b=Backend(settings=QSettings(str(tmp_path/'scope.ini'),QSettings.IniFormat),recovery_dir=tmp_path/'recovery');b.autosave_timer.stop()
    result=objects([box(0,0,10,15),box(10,0,20,15),box(20,0,30,15)])
    result.colors=[[100,180,100]]*10
    for f,group in zip(result.features,[1,1,10]):f['properties']['class_id']=group
    fields=[dict(key='height',label='Высота',type='number',default=None,required=False,choices=[]),
            dict(key='checked',label='Готово',type='boolean',default=None,required=False,choices=[])]
    result=change_schema(result,fields)
    result.features[0]['properties'].update(note='Первое',height=0,checked=False)
    result.features[1]['properties'].update(note='Второе',height=None,checked=None)
    b.raster=result.raster;b.notify(width=100,height=60);b.set_result(result)
    return app,b


def cleanup(b):
    b.timer.stop();b.web_map.close();b.basemap_loader.close();b.pool.shutdown(wait=True,cancel_futures=True);b.temp.cleanup()


def test_exact_group_mixed_values_partial_patch_empty_zero_false_and_undo_redo(tmp_path):
    app,b=setup(tmp_path)
    scope=json.dumps(dict(mode='group',group=1))
    try:
        data=b.attributeScope(scope)
        assert data['count']==2 and set(data['mixed'])=={'note','height','checked'}
        untouched=b.result.features[2]['properties'].copy()
        assert b.applyAttributeScope(scope,json.dumps(dict(height='12,5',checked=True)),True,data['epoch'])
        assert [f['properties']['height'] for f in b.result.features]==[0,12.5,None]
        assert [f['properties']['checked'] for f in b.result.features]==[False,True,None]
        assert [f['properties']['note'] for f in b.result.features[:2]]==['Первое','Второе']
        assert b.result.features[2]['properties']==untouched
        assert len(b.undo_stack)==1
        b.undo();assert b.result.features[1]['properties']['height'] is None
        b.redo();assert b.result.features[1]['properties']['height']==12.5
        data=b.attributeScope(scope)
        assert b.applyAttributeScope(scope,json.dumps(dict(note='Всем')),False,data['epoch'])
        assert [f['properties']['note'] for f in b.result.features]==['Всем','Всем','']
    finally:cleanup(b)


def test_validation_and_stale_scope_are_atomic_and_filter_combines_exact_group(tmp_path):
    app,b=setup(tmp_path)
    scope=json.dumps(dict(mode='group',group=1))
    try:
        before=[f['properties'].copy() for f in b.result.features];epoch=b.edit_revision
        for patch in [dict(note='Нельзя частично применить',height='нет'),dict(note='Нельзя',object_id=99)]:
            assert not b.applyAttributeScope(scope,json.dumps(patch),False,epoch)
            assert before==[f['properties'] for f in b.result.features] and not b.undo_stack
        b.selected=0;b.saveAttributes('Новая запись','{}')
        assert not b.applyAttributeScope(scope,json.dumps(dict(note='Устарело')),False,epoch)
        b.filterTable('','all',False,1)
        assert len(b.table.rows)==2
        filtered=json.dumps(dict(mode='filtered',group=1,query='Новая запись',review='edited',missing=False))
        data=b.attributeScope(filtered);assert data['count']==1
        assert b.applyAttributeScope(filtered,json.dumps(dict(height=21)),False,data['epoch'])
        assert b.result.features[0]['properties']['height']==21 and b.result.features[1]['properties']['height'] is None
        assert b.result.features[2]['properties']['height'] is None
    finally:cleanup(b)
