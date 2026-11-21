import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import rasterio
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from geotrace import app as module
from geotrace.app import Backend
from geotrace.core import save_preview, segment
from geotrace.project import load_project
from test_core import sample


def test_local_background_pan_zoom_save_and_preview_are_independent(tmp_path):
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend(recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent/'qml/Main.qml')))
    window = engine.rootObjects()[0]
    window.setProperty('width',1100)
    QTest.qWait(80)
    def visual(name, item=None):
        item = item or window.contentItem()
        if item.objectName() == name:
            return item
        for child in item.childItems():
            match = visual(name, child)
            if match is not None:
                return match
    def wait_background():
        for _ in range(300):
            QTest.qWait(50)
            assert not backend.data['basemapError'], backend.data['basemapError']
            if backend.data['basemapUrl'] and not backend.data['basemapLoading']:
                QTest.qWait(100)
                return
        assert False, backend.data
    def wait_job():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:
                break
        assert not backend.data['busy'] and not backend.data['error'], backend.data['status']
    try:
        raster = sample()
        source = tmp_path/'спутник.tif'
        with rasterio.open(source, 'w', driver='GTiff', width=100, height=60, count=3,
                           dtype='uint8', crs=raster.crs_wkt, transform=raster.transform) as dst:
            dst.write(np.moveaxis(raster.rgb, -1, 0))
        save_preview(raster, Path(backend.temp.name)/'raster.png')
        backend.kind = 'load'
        backend.future = Future()
        backend.future.set_result(raster)
        backend.poll()
        backend.auto_preview = segment(raster, 2, 10)
        preview = backend.auto_preview
        backend.auto_revision = backend.edit_revision
        backend.notify(autoReady=True)
        revision = backend.edit_revision
        backend.openBasemapFile(QUrl.fromLocalFile(str(source)))
        wait_background()
        assert backend.auto_preview is preview and backend.edit_revision == revision
        assert backend.view['raster_opacity'] == .65
        assert visual('basemapImage').isVisible()
        assert visual('basemapCredit').isVisible()
        rect = backend.data['basemapRect']
        assert rect == dict(x=-8, y=-5, w=116, h=70)
        viewport = window.findChild(QObject, 'mapViewport')
        old = backend.data['basemapUrl']
        viewport.setProperty('zoom', 12.)
        QTest.qWait(600)
        wait_background()
        assert backend.data['basemapUrl'] != old
        assert backend.data['basemapRect']['w'] < 100
        viewport.setProperty('contentX', 0.)
        viewport.setProperty('contentY', 0.)
        QTest.qWait(600)
        wait_background()
        assert backend.data['basemapRect']['x'] == -8
        viewport.setProperty('contentX', viewport.property('contentWidth')-viewport.width())
        viewport.setProperty('contentY', viewport.property('contentHeight')-viewport.height())
        QTest.qWait(600)
        wait_background()
        rect = backend.data['basemapRect']
        assert rect['x']+rect['w'] == 108 and rect['y']+rect['h'] == 65
        backend.setLayer('basemap_visible', False)
        assert not backend.data['basemapUrl'] and not visual('basemapImage').isVisible()
        assert backend.auto_preview is preview
        saved = tmp_path/'project.geotrace'
        backend.saveProject(QUrl.fromLocalFile(str(saved)))
        wait_job()
        restored = load_project(saved)
        assert restored.view['basemap_file'] == str(source)
        assert restored.view['basemap_source'] == 'local'
        assert not restored.view['basemap_visible']
        assert not warnings, warnings
    finally:
        backend.timer.stop()
        backend.basemap_loader.close()
        window.setProperty('closingConfirmed', True)
        window.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        del engine
        backend.temp.cleanup()
