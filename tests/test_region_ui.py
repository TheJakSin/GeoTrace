import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from concurrent.futures import Future
from pathlib import Path
import numpy as np
from PIL import Image

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shapely.geometry import box
from shapely.ops import unary_union

from geotrace import app as module
from geotrace.app import Backend
from geotrace.core import save_preview, save_overlay, segment
from geotrace.editing import transform_geometry
from geotrace.project import load_project
from test_core import sample
from test_region import kml


def test_kml_preview_apply_clip_undo_clear_save_reopen_and_layer_controls(tmp_path):
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

    def visual(name, item=None):
        item = item or window.contentItem()
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = visual(name, child)
            if found is not None:
                return found

    def click(name):
        item = visual(name)
        assert item is not None and item.isEnabled() and item.isVisible(), name
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                         item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())
        QTest.qWait(100)

    def wait():
        for _ in range(400):
            QTest.qWait(50)
            if not backend.data['busy']:
                break
        assert not backend.data['busy'] and not backend.data['error'], backend.data['status']
        QTest.qWait(100)

    try:
        raster = sample()
        save_preview(raster, Path(backend.temp.name)/'raster.png')
        backend.kind = 'load'
        backend.future = Future()
        backend.future.set_result(raster)
        backend.poll()
        old = segment(raster, 2, 10)
        old.features[0]['properties'].update(note='Ручная подпись', review='edited')
        save_overlay(old, Path(backend.temp.name)/'overlay.png')
        backend.set_result(old)
        geometry = transform_geometry(box(8.3, 7.4, 86.8, 48.7), raster.transform)
        path = kml(tmp_path/'участок.kml', [geometry])
        backend.loadArea(QUrl.fromLocalFile(str(path)))
        wait()
        dialog = window.findChild(QObject, 'areaDialog')
        assert dialog.property('visible') and backend.data['areaDraftUrl']
        assert backend.data['areaDraftRasterUrl']
        assert not visual('polygonOverlay').isVisible()
        assert 'area-draft-raster' in visual('rasterImage').property('source').toString()
        assert backend.raster.area is None and backend.result is old
        click('applyAreaButton')
        wait()
        assert backend.raster.area is not None and backend.data['areaName']
        assert not backend.data['areaDraftRasterUrl']
        assert not np.asarray(Image.open(Path(backend.temp.name)/'raster.png'))[~backend.raster.valid].any()
        assert unary_union([f['geometry'] for f in backend.result.features]).difference(geometry).area < 1e-12
        assert next(f for f in backend.result.features if f['properties']['object_id']==old.features[0]['properties']['object_id'])['properties']['note']=='Ручная подпись'
        assert backend.data['canUndo']
        backend.undo()
        assert backend.raster is raster and backend.result is old
        assert np.asarray(Image.open(Path(backend.temp.name)/'raster.png'))[raster.valid, 3].min() == 255
        backend.loadArea(QUrl.fromLocalFile(str(path)))
        wait()
        click('applyAreaButton')
        wait()
        backend.clearArea()
        wait()
        assert backend.raster.area is None and backend.raster.valid.sum()==raster.valid.sum()
        backend.undo()
        assert backend.raster.area is not None
        scroll = window.findChild(QObject, 'sidebarScroll').property('contentItem')
        click('layersButton')
        scroll.setProperty('contentY', 0.)
        QTest.qWait(100)
        preview = backend.result
        backend.auto_preview = preview
        backend.auto_revision = backend.edit_revision
        backend.notify(autoReady=True, autoPreviewUrl=backend.url('overlay.png'))
        revision = backend.edit_revision
        click('rasterVisible')
        assert not backend.view['raster_visible']
        assert backend.auto_preview is preview and backend.edit_revision == revision
        assert backend.auto_revision == revision
        slider = visual('rasterOpacity')
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                         slider.mapToScene(QPointF(slider.width()*.35, slider.height()/2)).toPoint())
        QTest.qWait(100)
        assert .2 < backend.view['raster_opacity'] < .5
        saved_path = tmp_path/'готово.geotrace'
        backend.saveProject(QUrl.fromLocalFile(str(saved_path)))
        wait()
        saved = load_project(saved_path)
        assert saved.raster.area is not None and not saved.view['raster_visible']
        backend.openFile(QUrl.fromLocalFile(str(saved_path)))
        wait()
        assert not visual('rasterVisible').property('checked')
        assert backend.data['areaUrl'] and backend.raster.area is not None
        backend.setLayer('raster_opacity', .77)
        assert backend.edit_revision == backend.autosaved_revision and backend.view_dirty
        backend.autosave()
        wait()
        assert not backend.view_dirty
        assert load_project(backend.recovery_path).view['raster_opacity'] == .77
        assert not warnings, warnings
    finally:
        backend.timer.stop()
        window.setProperty('closingConfirmed', True)
        window.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        del engine
        backend.temp.cleanup()
