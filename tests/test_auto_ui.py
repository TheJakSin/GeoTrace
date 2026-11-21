import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from concurrent.futures import Future
from pathlib import Path
from PySide6.QtCore import QObject, QPointF, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from geotrace import app as app_module
from geotrace.app import Backend
from geotrace.core import save_preview, save_overlay, segment
from geotrace.project import load_project
from geotrace.attributes import change_schema, SUGGESTED_FIELDS
from test_core import sample


def test_auto_preview_apply_discard_stale_undo_and_save(tmp_path):
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend(recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    engine = QQmlApplicationEngine(); warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend',backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent/'qml/Main.qml')))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    def visual(name,item=None):
        item=item or window.contentItem()
        if item.objectName()==name:return item
        for child in item.childItems():
            found=visual(name,child)
            if found is not None:return found
    def click(name):
        item=visual(name)
        assert item is not None and item.isEnabled() and item.isVisible(),name
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
        QTest.qWait(80)
    def wait():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:break
        assert not backend.data['busy'] and not backend.data['error'],backend.data['status']
        QTest.qWait(100)
    try:
        raster=sample(); save_preview(raster,Path(backend.temp.name)/'raster.png')
        backend.kind='load'; backend.future=Future(); backend.future.set_result(raster); backend.poll()
        old=change_schema(segment(raster,2,10), SUGGESTED_FIELDS[:2]); old.features[0]['properties'].update(review='edited',unit_name='Проверенный объект')
        save_overlay(old,Path(backend.temp.name)/'overlay.png'); backend.set_result(old)
        window.findChild(QObject,'autoProfile').setProperty('currentIndex',1)
        QTest.qWait(200)
        click('autoPreviewButton'); wait()
        assert backend.data['autoReady'] and backend.result is old
        assert 'сохранено: 1' in backend.data['autoSummary']
        assert 'auto-preview.png' in visual('polygonOverlay').property('source').toString()
        click('showAutoPreview')
        assert 'overlay.png' in visual('polygonOverlay').property('source').toString()
        # Saving while previewing writes the accepted state, never the draft.
        path=tmp_path/'before.geotrace'; backend.saveProject(QUrl.fromLocalFile(str(path))); wait()
        assert backend.data['autoReady']
        assert [f['properties'] for f in load_project(path).result.features]==[f['properties'] for f in old.features]
        click('autoDiscardButton')
        assert backend.result is old and not backend.data['autoReady'] and not backend.data['dirty']
        click('autoPreviewButton'); wait(); click('autoAcceptButton')
        assert backend.result is not old and not backend.data['autoReady'] and backend.data['dirty']
        assert backend.result.features[0]['properties']==old.features[0]['properties']
        backend.undo(); assert backend.result is old
        click('autoPreviewButton'); wait()
        backend.selected=1; backend.updateAttributes('X','Добавлено после расчёта','')
        assert not backend.data['autoReady']
        backend.acceptAuto(); assert backend.result is old and old.features[1]['properties']['unit_index']=='X'
        # First run can be undone back to a raster without any result.
        backend.set_result(None); backend.undo_stack.clear()
        click('autoPreviewButton'); wait(); click('autoAcceptButton'); backend.undo()
        assert backend.result is None
        assert not warnings,warnings
    finally:
        backend.timer.stop(); backend.autosave_timer.stop()
        window.setProperty('closingConfirmed',True); window.close()
        del window; del engine
        backend.pool.shutdown(wait=True,cancel_futures=True); backend.temp.cleanup()
