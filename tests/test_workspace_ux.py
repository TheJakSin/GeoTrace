import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')

from contextlib import contextmanager
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings,QUrl,QObject,QPointF,Qt,QCoreApplication
from PySide6.QtGui import QGuiApplication,QNativeGestureEvent,QPointingDevice,QInputDevice
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shapely.geometry import Point

from geotrace.workspace_app import WorkspaceBackend,build_workspace
from geotrace.spatial import project_geometry
from test_document import two_sheets

_application = None

@contextmanager
def scene(tmp_path):
    global _application
    if QQuickStyle.name()!='Basic':QQuickStyle.setStyle('Basic')
    _application=QGuiApplication.instance() or _application or QGuiApplication([])
    b=WorkspaceBackend(settings=QSettings(str(tmp_path/'test.ini'),QSettings.IniFormat),recovery_dir=tmp_path/'recovery')
    b.autosave_timer.stop();engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
    engine.rootContext().setContextProperty('backend',b)
    engine.load(QUrl.fromLocalFile(str(Path(__import__('geotrace').__file__).parent/'qml/Main.qml')))
    assert engine.rootObjects(),warnings
    w=engine.rootObjects()[0]
    b._install(build_workspace(two_sheets(),b.temp.name),fit=True);QTest.qWait(150)
    try:
        yield b,w,warnings
        assert not warnings,warnings
    finally:
        w.setProperty('closingConfirmed',True);w.close()
        __import__('shiboken6').delete(engine)
        b.timer.stop();b.autosave_timer.stop();b.basemap_loader.close();b.web_map.close();b.close_details()
        b.pool.shutdown(wait=True,cancel_futures=True);b.temp.cleanup()
        __import__('shiboken6').delete(b)
        QCoreApplication.sendPostedEvents(None,__import__('PySide6.QtCore',fromlist=['QEvent']).QEvent.DeferredDelete)


def wait(b):
    for _ in range(500):
        QTest.qWait(20)
        if not b.data['busy']:break
    assert not b.data['busy'] and not b.data['error'],b.data['status']


def test_matching_groups_on_map_removes_duplicates_and_completed_choices(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        b.beginGroupMatching();QTest.qWait(50)
        panel=w.findChild(QQuickItem,'workspaceGroupsDialog');assert panel.isVisible()
        canvas=w.findChild(QQuickItem,'rasterCanvas');viewport=w.findChild(QQuickItem,'mapViewport')
        for lon,expected in ((56.06,'a'),(56.095,'b')):
            point=project_geometry(Point(lon,59.99),'EPSG:4326')
            x,y=(~b.raster.transform)*(point.x,point.y);scale=float(viewport.property('zoom'))
            screen=canvas.mapToScene(QPointF(x*scale,y*scale)).toPoint()
            QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,screen);QTest.qWait(60)
            record=b.data['matchingA' if expected=='a' else 'matchingB']
            assert record['sheet']==expected and record['group']>0
        a,b_group=dict(b.data['matchingA']),dict(b.data['matchingB'])
        b.applyMatchedGroups(0,'Общий класс');wait(b)
        cid=b.document.sheet('a').groups[a['group']]
        assert b.document.sheet('b').groups[b_group['group']]==cid
        before=[f for f in b.result.features if f['properties']['class_id']==cid]
        assert len(before)==2
        assert b.data['matchingA']['group']==b.data['matchingB']['group']==0
        assert len(b.document.classes)==3
        matched=[r for r in b.data['classMappings'] if r['matched']]
        assert {(r['sheet'],r['group']) for r in matched}=={(a['sheet'],a['group']),(b_group['sheet'],b_group['group'])}
        b.stitchMatchedGroups();wait(b)
        assert len([f for f in b.result.features if f['properties']['class_id']==cid])==2
        assert not b.document.dissolve_classes
        b.endGroupMatching();QTest.qWait(30);assert not panel.isVisible()


def test_undo_restores_line_highlight_and_autosave_restores_status(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        b.saveLineType(json.dumps(dict(name='Разлом',colors=['#b53239'],style={})))
        b.setLineMode(True);b.setLineTool('draw');b.lineClick(300,200,8);b.lineClick(600,300,8);b.finishLine()
        original=b.document.lines[0]['geometry'];highlight=(Path(b.temp.name)/'line-selection.png').read_bytes()
        b.setLineTool('vertex');assert b.beginLineDrag(450,250,8)
        b.previewLineDrag(20,15);assert b.data['lineDragPreview']
        b.finishLineDrag(20,15);assert not b.document.lines[0]['geometry'].equals_exact(original,0)
        b.undo();QTest.qWait(40)
        assert b.document.lines[0]['geometry'].equals_exact(original,0)
        assert not b.data['lineDragPreview'] and not b.data['lineDraft']
        assert (Path(b.temp.name)/'line-selection.png').read_bytes()==highlight
        b.notify(status='Граница проверена.');b.autosave();wait(b)
        assert b.data['status']=='Граница проверена.'
        assert 'Копия восстановления' in b.data['autosaveStatus']


def test_native_trackpad_pinch_zooms_at_pointer(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        handler=w.findChild(QObject,'mapPinch');assert handler is not None
        viewport=w.findChild(QQuickItem,'mapViewport')
        before=float(viewport.property('zoom'))
        local=viewport.mapToScene(QPointF(viewport.width()*.4,viewport.height()*.3))
        canvas=w.findChild(QQuickItem,'rasterCanvas')
        anchor=canvas.mapFromScene(local)/before
        global_pos=QPointF(w.mapToGlobal(local.toPoint()))
        device=QPointingDevice('Test trackpad',418,QInputDevice.DeviceType.TouchPad,QPointingDevice.PointerType.Finger,
            QInputDevice.Capability.Position|QInputDevice.Capability.Scroll,2,0)
        for kind,value in ((Qt.BeginNativeGesture,0.),(Qt.ZoomNativeGesture,.25),(Qt.EndNativeGesture,0.)):
            event=QNativeGestureEvent(kind,device,2,local,local,global_pos,value,QPointF(),sequenceId=1)
            QCoreApplication.sendEvent(w,event);QTest.qWait(20)
        assert float(viewport.property('zoom'))>before*1.1
        after=canvas.mapFromScene(local)/float(viewport.property('zoom'))
        assert abs(after.x()-anchor.x())<1 and abs(after.y()-anchor.y())<1
        button=w.findChild(QQuickItem,'zoomOutButton')
        pos=button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint()
        for _ in range(45):QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,pos)
        assert float(viewport.property('zoom'))==pytest.approx(.015)
        center=canvas.mapToScene(QPointF(canvas.width()/2,canvas.height()/2))
        expected=viewport.mapToScene(QPointF(viewport.width()/2,viewport.height()/2))
        assert abs(center.x()-expected.x())<2 and abs(center.y()-expected.y())<2
        origin=viewport.mapToScene(QPointF(60,90)).toPoint();end=viewport.mapToScene(QPointF(160,160)).toPoint()
        QTest.mousePress(w,Qt.LeftButton,Qt.NoModifier,origin);QTest.mouseMove(w,end,30);QTest.mouseRelease(w,Qt.LeftButton,Qt.NoModifier,end)
        moved=canvas.mapToScene(QPointF(canvas.width()/2,canvas.height()/2))
        assert moved.x()-center.x()==pytest.approx(100,abs=2)
        assert moved.y()-center.y()==pytest.approx(70,abs=2)


def test_line_inspector_is_nonmodal_readable_and_keeps_selection(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        b.saveLineType(json.dumps(dict(name='Разлом',colors=['#b53239'],style={})))
        b.setLineMode(True);b.setLineTool('draw');b.lineClick(300,200,8);b.lineClick(600,300,8);b.finishLine();QTest.qWait(60)
        action=w.findChild(QQuickItem,'linePropertiesAction');assert action.isVisible() and action.isEnabled()
        QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,action.mapToScene(QPointF(action.width()/2,action.height()/2)).toPoint());QTest.qWait(100)
        panel=w.findChild(QObject,'workspaceLineAttributes');assert panel.property('visible') and not panel.property('modal')
        assert b.data['lineSelected']==[1] and panel.property('draft').toVariant()['object_id']==1
        field=w.findChild(QQuickItem,'lineAttributeName');assert field.property('color').name()=='#263445'
        field.forceActiveFocus();QTest.keyClick(w,Qt.Key_F);QTest.qWait(50)
        apply=w.findChild(QQuickItem,'lineApplyAttributes');assert apply.isEnabled()
        QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,apply.mapToScene(QPointF(apply.width()/2,apply.height()/2)).toPoint());QTest.qWait(50)
        assert b.document.lines[0]['properties']['name']=='f'
        assert panel.property('visible')
