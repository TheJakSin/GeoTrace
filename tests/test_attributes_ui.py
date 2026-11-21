import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from concurrent.futures import Future
from pathlib import Path
from PySide6.QtCore import QObject,QPointF,QUrl,Qt,QMetaObject,Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from geotrace import app as app_module
from geotrace.app import Backend
from geotrace.core import save_preview,save_overlay,segment
from geotrace.project import load_project
from test_core import sample


def test_field_dialog_typed_editor_validation_and_undo(tmp_path):
    QQuickStyle.setStyle('Basic');application=QGuiApplication.instance() or QGuiApplication([])
    backend=Backend(recovery_dir=tmp_path/'recovery');backend.autosave_timer.stop()
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend',backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent/'qml/Main.qml')))
    assert engine.rootObjects(),warnings
    window=engine.rootObjects()[0]
    def button(name):
        item=window.findChild(QQuickItem,name)
        assert item is not None and item.isEnabled() and item.isVisible(),name
        # Allow hover/layout to settle before sending a real press and release.
        # An instantaneous mouseClick can miss a moving Quick control on CI.
        QTest.mouseMove(window,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
        QTest.qWait(60)
        point=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
        if not item.property('pressed'):
            Path('test-screenshots').mkdir(exist_ok=True)
            window.grabWindow().save('test-screenshots/attributes-'+name+'.png')
        assert item.property('pressed'), (name,point)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
        QTest.qWait(70)
    def visual(name, item=None):
        item = item or window.contentItem()
        if item.objectName() == name: return item
        for child in item.childItems():
            found = visual(name,child)
            if found is not None: return found
        return None
    def wait():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']: break
        assert not backend.data['busy'] and not backend.data['error'],backend.data['status']
    try:
        raster=sample();result=segment(raster,2,10)
        save_preview(raster,Path(backend.temp.name)/'raster.png')
        backend.kind='load';backend.future=Future();backend.future.set_result(raster);backend.poll()
        save_overlay(result,Path(backend.temp.name)/'overlay.png')
        backend.kind='segment';backend.future=Future();backend.future.set_result(result);backend.poll()
        QTest.qWait(100)
        button('attributesToggle');button('manageFieldsButton');button('addFieldButton')
        preset=window.findChild(QObject,'fieldPreset')
        i=1+next(i for i,f in enumerate(backend.data['suggestions']) if f['key']=='thickness_m')
        preset.setProperty('currentIndex',i)
        assert QMetaObject.invokeMethod(preset,'activated',Qt.DirectConnection,Q_ARG(int,i))
        assert window.findChild(QObject,'fieldKey').property('text')=='thickness_m'
        window.findChild(QObject,'fieldDefault').setProperty('text','12,5')
        button('saveFieldButton')
        for _ in range(10):
            if not window.findChild(QObject,'fieldDialog').property('visible'): break
            QTest.qWait(50)
        assert not window.findChild(QObject,'fieldDialog').property('visible'), (backend.data['schemaError'],backend.data['fields'],warnings)
        assert backend.result.fields[0]['type']=='number'
        assert backend.result.features[0]['properties']['thickness_m']==12.5
        assert QMetaObject.invokeMethod(window.findChild(QObject,'schemaManager'),'close',Qt.DirectConnection)
        QTest.qWait(200)
        backend.selectAt(75,30);QTest.qWait(50)
        input=visual('attribute_thickness_m')
        assert input is not None and input.property('text')=='12.5'
        input.setProperty('text','ошибка');button('applyAttributesButton')
        assert backend.data['error'] and backend.result.features[backend.selected]['properties']['thickness_m']==12.5
        input.setProperty('text','25,6');button('applyAttributesButton')
        assert not backend.data['error']
        assert backend.result.features[backend.selected]['properties']['thickness_m']==25.6
        input.setProperty('text','33,3')
        backend.autosave();wait()
        assert visual('attribute_thickness_m') is input
        assert input.property('text')=='33,3', 'Autosave must not recreate field editors or erase unsubmitted text'
        button('applyAttributesButton')
        path=tmp_path/'fields.geotrace';backend.saveProject(QUrl.fromLocalFile(str(path)));wait()
        assert load_project(path).result.fields==backend.result.fields
        backend.removeField('thickness_m')
        assert backend.result.fields==[]
        backend.undo()
        assert backend.result.fields[0]['key']=='thickness_m'
        assert any(f['properties']['thickness_m']==33.3 for f in backend.result.features)
        assert not warnings,warnings
    finally:
        backend.timer.stop();backend.autosave_timer.stop()
        window.setProperty('closingConfirmed',True);window.close()
        del window;del engine
        backend.pool.shutdown(wait=True,cancel_futures=True);backend.temp.cleanup()
