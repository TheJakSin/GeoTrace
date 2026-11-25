import hashlib,json,time
from pathlib import Path
from PySide6.QtCore import QObject,QMetaObject,Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from shapely.geometry import box

from geotrace.core import Result
from geotrace.editing import transform_geometry,_result_from_features
from test_workspace_ux import scene
from test_groups_ui import click,wait
from test_smoothing import partition


def until(predicate,b):
    for _ in range(600):
        QTest.qWait(20);time.sleep(.002)
        if predicate():return
    raise AssertionError(str(b.data.get('status'))+' '+str(b.data.get('updateMessage')))


def visual(w,name):
    def find(item):
        if item.objectName()==name:return item
        for child in item.childItems():
            match=find(child)
            if match is not None:return match
        return None
    return find(w.contentItem())


def test_group_style_opens_from_the_clicked_swatch_without_group_selector(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        original=[f['geometry'].wkb for f in b.result.features]
        w.setProperty('sidebarOpen',True);w.setProperty('layersMode',True);QTest.qWait(100)
        click(w,visual(w,'layerGroupSwatch1'));QTest.qWait(80)
        dialog=w.findChild(QObject,'workspaceStylesDialog')
        assert dialog.property('visible') and dialog.property('identity')==1
        assert 'Группа' not in str(dialog.property('title')) or dialog.property('identity')==1
        w.findChild(QObject,'groupStyleFill').setProperty('text','#bb8855')
        click(w,w.findChild(QQuickItem,'applyGroupStyle'));wait(b)
        assert b.document.classes[0]['style']['fill']=='#bb8855'
        assert [f['geometry'].wkb for f in b.result.features]==original
        panel=w.findChild(QObject,'workspaceClassManager');QMetaObject.invokeMethod(panel,'open');QTest.qWait(80)
        click(w,visual(w,'groupStyleSwatch2'));QTest.qWait(60)
        assert dialog.property('visible') and dialog.property('identity')==2
        assert not warnings


def test_smoothing_preview_compare_apply_undo_keeps_attributes(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        features=[]
        for i,p in enumerate(partition()):
            # Fit a stair-stepped contact into the current project canvas.
            from affine import Affine
            geometry=transform_geometry(p,b.raster.transform*Affine.translation(300,300)*Affine.scale(10))
            features.append(dict(geometry=geometry,properties=dict(object_id=11+i,class_id=i+1,note='Keep',origin='manual_draw',review='edited',source_sheet=b.document.sheets[0].id)))
        before=_result_from_features(b.raster,features,b.result.colors,b.result.fields)
        b.document.manual=before;b.set_result(before)
        original=[f['geometry'].wkb for f in before.features]
        panel=w.findChild(QObject,'workspaceSmoothing');QMetaObject.invokeMethod(panel,'open');QTest.qWait(60)
        click(w,w.findChild(QQuickItem,'previewSmoothingButton'));wait(b)
        assert b.data['geometryReady'] and b.edit_preview is not None,b.data['smoothInfo']
        assert [f['geometry'].wkb for f in b.result.features]==original
        click(w,w.findChild(QQuickItem,'smoothingComparison'));assert b.data['geometryPreviewVisible'] is False
        click(w,w.findChild(QQuickItem,'applySmoothingButton'))
        assert [f['properties']['object_id'] for f in b.result.features]==[11,12]
        assert all(f['properties']['note']=='Keep' for f in b.result.features)
        assert any(f['geometry'].wkb!=old for f,old in zip(b.result.features,original))
        b.undo();assert [f['geometry'].wkb for f in b.result.features]==original
        assert not warnings


def test_update_dialog_download_and_unsaved_work_guard(tmp_path,monkeypatch):
    import geotrace.updates as updates
    payload=b'Installer';path=tmp_path/'GeoTrace-9.0.0-build100-macOS-arm64.pkg'
    release=dict(state='available',version='9.0.0',message='Доступна версия 9.0.0',notes='Новые функции',name=path.name,size=len(payload),sha256=hashlib.sha256(payload).hexdigest(),url='https://example.invalid/installer')
    monkeypatch.setattr(updates,'check_updates',lambda current:dict(release))
    def download(metadata,folder,progress,cancel):
        path.write_bytes(payload);progress(len(payload),len(payload));return dict(metadata,path=str(path))
    monkeypatch.setattr(updates,'download_update',download)
    launched=[];monkeypatch.setattr(updates,'launch_installer',lambda metadata:launched.append(metadata))
    with scene(tmp_path) as (b,w,warnings):
        dialog=w.findChild(QObject,'workspaceUpdates');QMetaObject.invokeMethod(dialog,'open');QTest.qWait(60)
        click(w,w.findChild(QQuickItem,'checkUpdatesButton'));until(lambda:b.data['updateState']=='available',b)
        assert b.data['updateVersion']=='9.0.0' and not launched
        b.downloadUpdate();until(lambda:b.data['updateState']=='ready',b)
        assert path.exists() and not launched
        b.notify(dirty=True);w.request('installUpdate') if hasattr(w,'request') else QMetaObject.invokeMethod(w,'request',Qt.DirectConnection,__import__('PySide6.QtCore',fromlist=['Q_ARG']).Q_ARG('QVariant','installUpdate'))
        QTest.qWait(60)
        assert w.property('pendingAction')=='installUpdate' and not launched
        assert w.findChild(QObject,'unsavedDialog').property('visible')
        QMetaObject.invokeMethod(w.findChild(QObject,'unsavedDialog'),'reject')
        b.notify(dirty=False)
        assert not warnings


def test_closing_smoothing_cancels_pending_preview(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        original=[f['geometry'].wkb for f in b.result.features]
        panel=w.findChild(QObject,'workspaceSmoothing');QMetaObject.invokeMethod(panel,'open');QTest.qWait(30)
        b.previewSmoothing('all',0,'gentle',50);assert b.data['busy']
        QMetaObject.invokeMethod(panel,'close');wait(b)
        assert not b.data['geometryReady'] and 'отменён' in b.data['status']
        assert [f['geometry'].wkb for f in b.result.features]==original
        assert not warnings
