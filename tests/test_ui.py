"""Exercise actual QML selection and attribute bindings using a tiny two-region map."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from geotrace import app as app_module
from geotrace.app import Backend
from geotrace.core import save_preview, save_overlay, segment
from test_core import sample


def test_qml_polygon_selection_attribute_update_and_undo():
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend()
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
        canvas = window.findChild(QQuickItem, 'rasterCanvas')
        assert canvas is not None
        click = canvas.mapToScene(QPointF(canvas.width() * 0.75, canvas.height() * 0.5))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, click.toPoint())
        QTest.qWait(50)
        assert backend.selected >= 0
        backend.updateAttributes('O₂cr', 'Чертовская свита', 'Проверить контакт')
        QTest.qWait(50)
        assert window.findChild(QObject, 'indexField').property('text') == 'O₂cr'
        assert window.findChild(QObject, 'nameField').property('text') == 'Чертовская свита'
        assert backend.data['dirty']
        backend.undo()
        QTest.qWait(50)
        assert window.findChild(QObject, 'indexField').property('text') == ''
        original = backend.result
        window.findChild(QObject, 'modeTabs').setProperty('currentIndex', 1)
        QTest.qWait(100)
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
        assert not warnings
    finally:
        window.setProperty('closingConfirmed', True)
        window.close()
        del window
        del engine
        backend.pool.shutdown(wait=True, cancel_futures=True)
        backend.temp.cleanup()
