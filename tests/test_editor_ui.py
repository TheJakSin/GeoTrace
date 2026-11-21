"""Real QML selection, drawing, worker preview, apply and undo."""
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
from shapely.ops import unary_union
from geotrace import app as app_module
from geotrace.app import Backend
from geotrace.core import save_preview, save_overlay
from test_editing import baseline


def test_editor_clicks_preview_apply_and_undo(tmp_path):
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend(recovery_dir=tmp_path / 'recovery')
    backend.autosave_timer.stop()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent / 'qml' / 'Main.qml')))
    window = engine.rootObjects()[0]
    def click_item(name):
        item = window.findChild(QQuickItem, name)
        assert item is not None and item.isEnabled() and item.isVisible(), name
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.qWait(30)
    def click_map(x,y):
        canvas = window.findChild(QQuickItem, 'rasterCanvas')
        point = canvas.mapToScene(QPointF(canvas.width()*x/100, canvas.height()*y/60))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.qWait(30)
    def wait_job():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:
                break
        assert not backend.data['busy'] and not backend.data['error'], backend.data['status']
        QTest.qWait(60)
    try:
        result = baseline()
        save_preview(result.raster, Path(backend.temp.name)/'raster.png')
        backend.kind='load'; backend.future=Future(); backend.future.set_result(result.raster); backend.poll()
        save_overlay(result, Path(backend.temp.name)/'overlay.png')
        backend.kind='segment'; backend.future=Future(); backend.future.set_result(result); backend.poll()
        window.findChild(QObject, 'modeTabs').setProperty('currentIndex', 2)
        QTest.qWait(150)
        tool=window.findChild(QObject,'editTool')
        assert tool.property('currentIndex')==5 and backend.edit_tool==5
        canvas=window.findChild(QQuickItem,'rasterCanvas')
        press=canvas.mapToScene(QPointF(canvas.width()*.50+3,canvas.height()*.60)).toPoint()
        release=press+QPointF(20,3).toPoint()
        for single in (False,True):
            window.findChild(QObject,'singleEdge').setProperty('checked',single)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,press)
            assert backend.edge_drag is not None
            QTest.mouseMove(window,release,25)
            assert backend.result is result
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,release)
            wait_job()
            assert backend.result is not result and not backend.data['geometryReady']
            assert not backend.data['vertexPoints']
            assert all(f['properties']['origin']=='manual_boundary' for f in backend.result.features)
            backend.undo()
            assert backend.result is result
        tool.setProperty('currentIndex',0)
        click_map(10,35); click_map(80,35)
        assert len(backend.data['editSelected']) == 2
        click_item('mergePreviewButton'); wait_job()
        assert backend.result is result  # Preview cannot alter the map.
        assert backend.data['geometryReady'] and len(backend.edit_preview.features) == 1
        QTest.keyClick(window,Qt.Key_Return);QTest.qWait(30)
        assert len(backend.result.features) == 1 and backend.data['dirty']
        backend.undo()
        assert backend.result is result
        click_map(10,35)
        window.findChild(QObject, 'editTool').setProperty('currentIndex', 1)
        QTest.qWait(50)
        click_map(1,40); click_map(51,45)
        assert len(backend.edit_points) == 2
        click_item('linePreviewButton'); wait_job()
        assert backend.data['geometryReady'] and len(backend.edit_preview.features) == 3
        click_item('applyGeometryButton')
        assert len(backend.result.features) == 3
        backend.undo()
        window.findChild(QObject, 'editTool').setProperty('currentIndex', 0)
        click_map(10,35); click_map(80,35)
        window.findChild(QObject, 'editTool').setProperty('currentIndex', 2)
        QTest.qWait(50)
        click_map(50,1); click_map(65,30); click_map(50,59)
        click_item('linePreviewButton'); wait_job()
        assert backend.data['geometryReady']
        click_item('applyGeometryButton')
        before=unary_union([f['geometry'] for f in result.features])
        after=unary_union([f['geometry'] for f in backend.result.features])
        assert before.symmetric_difference(after).area < 1e-10
        assert all(f['properties']['origin']=='manual_boundary' for f in backend.result.features)
        backend.undo()
        assert backend.result is result
        assert not warnings
    finally:
        backend.timer.stop(); backend.autosave_timer.stop()
        window.setProperty('closingConfirmed',True); window.close()
        del window; del engine
        backend.pool.shutdown(wait=True,cancel_futures=True); backend.temp.cleanup()


def test_drawing_without_segmentation_and_real_vertex_drag(tmp_path):
    from shapely.geometry import Polygon, box
    from test_draw_vertex import objects
    from geotrace.project import load_project
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend(recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    engine = QQmlApplicationEngine()
    warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend',backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent/'qml/Main.qml')))
    window=engine.rootObjects()[0]
    def point(x,y):
        canvas=window.findChild(QQuickItem,'rasterCanvas')
        return canvas.mapToScene(QPointF(canvas.width()*x/100,canvas.height()*y/60)).toPoint()
    def click_map(x,y):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point(x,y)); QTest.qWait(30)
    def button(name):
        item=window.findChild(QQuickItem,name)
        assert item.isEnabled() and item.isVisible(),name
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
        QTest.qWait(30)
    def wait():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']: break
        assert not backend.data['busy'] and not backend.data['error'],backend.data['status']
        QTest.qWait(60)
    try:
        result=objects([Polygon([(40,0),(50,0),(50,30),(50,60),(40,60)]),box(50,0,90,60)])
        save_preview(result.raster,Path(backend.temp.name)/'raster.png')
        backend.kind='load';backend.future=Future();backend.future.set_result(result.raster);backend.poll()
        window.findChild(QObject,'modeTabs').setProperty('currentIndex',2)
        tool=window.findChild(QObject,'editTool');tool.setProperty('currentIndex',3)
        QTest.qWait(100)
        for x,y in [(5,5),(15,5),(15,15),(5,15)]: click_map(x,y)
        assert backend.result is None and len(backend.edit_points)==4
        button('linePreviewButton');wait()
        assert backend.data['geometryReady'] and len(backend.edit_preview.features)==1
        button('applyGeometryButton')
        assert len(backend.result.features)==1
        assert backend.selected == 0 and backend.data['selection']['object_id'] == 1
        backend.undo()
        assert backend.result is None and backend.data['count']==0
        save_overlay(result,Path(backend.temp.name)/'overlay.png')
        backend.kind='segment';backend.future=Future();backend.future.set_result(result);backend.poll()
        tool.setProperty('currentIndex',0)
        click_map(45,35)
        tool.setProperty('currentIndex',4);QTest.qWait(50)
        assert backend.data['vertexPoints']
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point(50,30));QTest.qWait(30)
        assert backend.vertex_source is not None and backend.autosave_paused
        QTest.mouseMove(window,point(60,30),50)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point(60,30))
        wait()
        assert backend.data['geometryReady'] and backend.result is result
        assert not backend.autosave_paused
        button('applyGeometryButton')
        assert all(f['properties']['origin']=='manual_vertex' for f in backend.result.features)
        assert backend.edit_selected and backend.data['vertexPoints']
        path=tmp_path/'Вершины.geotrace'
        backend.saveProject(QUrl.fromLocalFile(str(path)));wait()
        loaded=load_project(path).result
        for actual,expected in zip(loaded.features,backend.result.features):
            assert actual['geometry'].equals_exact(expected['geometry'],0)
            assert actual['properties']==expected['properties']
        backend.undo()
        assert backend.result is result
        viewport=window.findChild(QQuickItem,'mapViewport')
        window.setProperty('width',1100)
        viewport.setProperty('zoom',12);QTest.qWait(80)
        canvas=window.findChild(QQuickItem,'editCanvas')
        assert canvas.width() <= viewport.width() and canvas.height() <= viewport.height()
        assert not warnings,warnings
    finally:
        backend.timer.stop();backend.autosave_timer.stop()
        window.setProperty('closingConfirmed',True);window.close()
        del window;del engine
        backend.pool.shutdown(wait=True,cancel_futures=True);backend.temp.cleanup()


def test_real_lasso_additive_clicks_direct_bulk_merge_and_pan(tmp_path):
    from PySide6.QtCore import QSettings
    from shapely.geometry import box
    from test_draw_vertex import objects
    QQuickStyle.setStyle('Basic')
    application=QGuiApplication.instance() or QGuiApplication([])
    backend=Backend(settings=QSettings(str(tmp_path/'settings.ini'),QSettings.IniFormat),recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend',backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent/'qml/Main.qml')))
    window=engine.rootObjects()[0]
    def point(x,y):
        canvas=window.findChild(QQuickItem,'rasterCanvas')
        return canvas.mapToScene(QPointF(canvas.width()*x/100,canvas.height()*y/60)).toPoint()
    def click_map(x,y):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point(x,y));QTest.qWait(20)
    def lasso(points,modifiers=Qt.NoModifier):
        QTest.mousePress(window,Qt.LeftButton,modifiers,point(*points[0]))
        assert backend.autosave_paused
        for p in points[1:]: QTest.mouseMove(window,point(*p),25)
        QTest.mouseRelease(window,Qt.LeftButton,modifiers,point(*points[-1]));QTest.qWait(30)
        assert not backend.autosave_paused
    try:
        original=objects([box(0,0,10,15),box(10,0,20,15),box(20,0,30,15),box(40,0,50,15)])
        save_preview(original.raster,Path(backend.temp.name)/'raster.png')
        backend.kind='load';backend.future=Future();backend.future.set_result(original.raster);backend.poll()
        save_overlay(original,Path(backend.temp.name)/'overlay.png')
        backend.kind='segment';backend.future=Future();backend.future.set_result(original);backend.poll()
        window.findChild(QObject,'modeTabs').setProperty('currentIndex',2)
        window.findChild(QObject,'editTool').setProperty('currentIndex',0)
        viewport=window.findChild(QQuickItem,'mapViewport');viewport.setProperty('zoom',6);QTest.qWait(80)
        for x in (5,15,25,45): click_map(x,5)
        assert len(backend.edit_selected)==4
        click_map(45,5)
        assert len(backend.edit_selected)==3
        window.findChild(QObject,'lassoToggle').setProperty('checked',True)
        lasso([(1,1),(29,1),(29,14),(1,14),(1,1)])
        assert len(backend.edit_selected)==3
        lasso([(41,1),(49,1),(49,14),(41,14),(41,1)],Qt.ShiftModifier)
        assert len(backend.edit_selected)==4
        # A short click in lasso mode toggles exactly once.
        click_map(45,5)
        assert len(backend.edit_selected)==3
        merge=window.findChild(QQuickItem,'quickMergeButton')
        assert merge.isVisible() and merge.isEnabled()
        QTest.keyClick(window,Qt.Key_Return)
        for _ in range(200):
            QTest.qWait(30)
            if not backend.data['busy']:break
        assert not backend.data['busy'] and not backend.data['error'],backend.data['status']
        QTest.qWait(60)
        assert len(backend.result.features)==2 and not backend.data['geometryReady']
        backend.undo();assert backend.result is original
        window.setProperty('width',1100)
        viewport.setProperty('zoom',12);QTest.qWait(80)
        origin=point(30,25)
        before=viewport.property('contentX')
        QTest.mousePress(window,Qt.MiddleButton,Qt.NoModifier,origin)
        QTest.mouseMove(window,origin-QPointF(40,0).toPoint(),30)
        QTest.mouseRelease(window,Qt.MiddleButton,Qt.NoModifier,origin-QPointF(40,0).toPoint())
        assert viewport.property('contentX')>before
        assert not warnings,warnings
    finally:
        backend.timer.stop();backend.autosave_timer.stop();backend.web_map.close();backend.basemap_loader.close()
        window.setProperty('closingConfirmed',True);window.close();del window;del engine
        backend.pool.shutdown(wait=True,cancel_futures=True);backend.temp.cleanup()
