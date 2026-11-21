"""Exercise actual QML selection and attribute bindings using a tiny two-region map."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QUrl, Qt, QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from geotrace import app as app_module
from geotrace.app import Backend
from geotrace.core import save_preview, save_overlay, segment
from test_core import sample


def test_qml_polygon_selection_attribute_update_and_undo(tmp_path):
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend(recovery_dir=tmp_path / 'recovery')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent / 'qml' / 'Main.qml')))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        raster = sample()
        save_preview(raster, Path(backend.temp.name) / 'raster.png')
        backend.kind = 'load'
        backend.future = Future()
        backend.future.set_result(raster)
        backend.poll()
        QTest.qWait(100)
        result = segment(raster, 2, 10)
        save_overlay(result, Path(backend.temp.name) / 'overlay.png')
        backend.kind = 'segment'
        backend.future = Future()
        backend.future.set_result(result)
        backend.poll()
        QTest.qWait(100)
        QTest.keyClick(window, Qt.Key_V)
        canvas = window.findChild(QQuickItem, 'rasterCanvas')
        assert canvas is not None
        click = canvas.mapToScene(QPointF(canvas.width() * 0.75, canvas.height() * 0.5))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, click.toPoint())
        QTest.qWait(50)
        assert backend.selected >= 0
        backend.saveAttributes('Проверить контакт', '{}')
        QTest.qWait(50)
        assert window.findChild(QObject, 'noteField').property('text') == 'Проверить контакт'
        assert 'unit_name' not in backend.result.features[backend.selected]['properties']
        assert backend.data['dirty']
        backend.undo()
        QTest.qWait(50)
        assert window.findChild(QObject, 'noteField').property('text') == ''
        original = backend.result
        window.findChild(QObject, 'modeTabs').setProperty('currentIndex', 1)
        QTest.qWait(100)
        click = canvas.mapToScene(QPointF(canvas.width()*.75,canvas.height()*.5))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, click.toPoint())
        QTest.qWait(50)
        assert backend.data['hintCount'] == 1
        backend.addHint(10,10,0,512)
        backend.runPrompt(0,22)
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:
                break
        assert not backend.data['error'], backend.data['status']
        assert backend.data['proposalReady']
        assert backend.data['hintUrl']
        backend.acceptPrompt()
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:
                break
        assert not backend.data['error'], backend.data['status']
        assert any(f['properties']['review']=='accepted' for f in backend.result.features)
        assert not backend.data['proposalReady']
        backend.undo()
        assert backend.result is original
        assert not any(f['properties']['review']=='accepted' for f in backend.result.features)
        # Exercise real worker snapshots and recovery as part of this same UI scenario.
        def wait_job():
            for _ in range(200):
                QTest.qWait(50)
                if not backend.data['busy']:
                    break
            assert not backend.data['busy']
            assert not backend.data['error'], backend.data['status']

        project_path = tmp_path / 'Геология.geotrace'
        backend.saveProject(QUrl.fromLocalFile(str(project_path)))
        wait_job()
        assert project_path.exists() and not backend.data['dirty']
        backend.selectAt(75, 30)
        backend.updateAttributes('Q₄', 'Сохранённая правка', 'После первого сохранения')
        backend.autosave()
        wait_job()
        assert backend.recovery_path.exists() and backend.data['dirty']
        from geotrace.project import load_project
        snapshot = load_project(backend.recovery_path)
        assert any(f['properties']['unit_name'] == 'Сохранённая правка' for f in snapshot.result.features)
        assert snapshot.source_project == str(project_path)
        backend.exportFile(QUrl.fromLocalFile(str(tmp_path / 'export.gpkg')))
        wait_job()
        assert backend.data['dirty'], 'Export must not mark the project as saved'

        recovered = Backend(recovery_dir=tmp_path / 'recovery')
        recovered.autosave_timer.stop()
        try:
            assert recovered.data['recoveries']
            recovered.restoreRecovery(str(backend.recovery_path))
            for _ in range(200):
                QTest.qWait(50)
                if not recovered.data['busy']:
                    break
            assert not recovered.data['busy'] and not recovered.data['error'], recovered.data['status']
            assert recovered.data['dirty']
            assert recovered.project_path == str(project_path)
            assert any(f['properties']['unit_name'] == 'Сохранённая правка' for f in recovered.result.features)
        finally:
            recovered.timer.stop()
            recovered.pool.shutdown(wait=True, cancel_futures=True)
            recovered.temp.cleanup()

        save_button = window.findChild(QQuickItem, 'saveProjectButton')
        save_click = save_button.mapToScene(QPointF(save_button.width() / 2, save_button.height() / 2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, save_click.toPoint())
        wait_job()
        assert not backend.data['dirty'] and not backend.recovery_path.exists()
        backend.openFile(QUrl.fromLocalFile(str(project_path)))
        wait_job()
        assert not backend.data['dirty'] and not backend.data['canUndo']
        assert backend.data['hintCount'] == 0
        assert any(f['properties']['unit_name'] == 'Сохранённая правка' for f in backend.result.features)
        # Closing offers Save; autosave must not race the open dialog.
        backend.selectAt(75, 30)
        backend.updateAttributes('Q₄', 'Сохранено при закрытии', '')
        window.close()
        QTest.qWait(50)
        dialog = window.findChild(QObject, 'unsavedDialog')
        assert dialog.property('visible') and backend.autosave_paused
        backend.autosave()
        assert not backend.data['busy']
        assert QMetaObject.invokeMethod(dialog, 'accept', Qt.DirectConnection)
        wait_job()
        QTest.qWait(50)
        assert not window.isVisible()
        assert any(f['properties']['unit_name'] == 'Сохранено при закрытии'
                   for f in load_project(project_path).result.features)
        assert not warnings
    finally:
        backend.timer.stop()
        backend.autosave_timer.stop()
        window.setProperty('closingConfirmed', True)
        window.close()
        del window
        del engine
        backend.pool.shutdown(wait=True, cancel_futures=True)
        backend.temp.cleanup()
