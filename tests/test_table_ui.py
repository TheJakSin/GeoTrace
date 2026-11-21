import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software')
from concurrent.futures import Future
from pathlib import Path
from PySide6.QtCore import QObject,QPointF,QUrl,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from geotrace import app as app_module
from geotrace.app import Backend
from geotrace.core import save_preview,save_overlay
from geotrace.project import load_project
from test_table import data


def test_table_filter_bulk_preview_undo_and_map_focus(tmp_path):
    QQuickStyle.setStyle('Basic');application=QGuiApplication.instance() or QGuiApplication([])
    backend=Backend(recovery_dir=tmp_path/'recovery');backend.autosave_timer.stop()
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(e.toString() for e in items))
    engine.rootContext().setContextProperty('backend',backend)
    engine.load(QUrl.fromLocalFile(str(Path(app_module.__file__).parent/'qml/Main.qml')))
    assert engine.rootObjects(),warnings
    window=engine.rootObjects()[0]
    def visual(name,item=None):
        item=item or window.contentItem()
        if item.objectName()==name:return item
        for child in item.childItems():
            found=visual(name,child)
            if found is not None:return found
        return None
    def click(name):
        item=visual(name)
        assert item is not None and item.isEnabled() and item.isVisible(),name
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint());QTest.qWait(80)
    def invoke(name,method):
        assert QMetaObject.invokeMethod(window.findChild(QObject,name),method,Qt.DirectConnection)
    def wait():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:break
        assert not backend.data['busy'] and not backend.data['error'],backend.data['status']
    try:
        result=data();raster=result.raster
        save_preview(raster,Path(backend.temp.name)/'raster.png')
        backend.kind='load';backend.future=Future();backend.future.set_result(raster);backend.poll()
        save_overlay(result,Path(backend.temp.name)/'overlay.png')
        backend.kind='segment';backend.future=Future();backend.future.set_result(result);backend.poll()
        QTest.qWait(150);click('openTableButton');QTest.qWait(250)
        assert backend.data['tableMatched']==2
        window.findChild(QObject,'tableSearch').setProperty('text','ПЕСЧАНИК')
        invoke('polygonDialog','filterNow');QTest.qWait(100)
        assert backend.data['tableMatched']==1
        click('selectFilteredButton')
        assert backend.table.selected_ids=={result.features[0]['properties']['object_id']}
        window.findChild(QObject,'bulkField').setProperty('currentIndex',next(i for i,f in enumerate(backend.data['bulkFields']) if f['key']=='age'));QTest.qWait(50)
        bulk=window.findChild(QQuickItem,'bulkValue')
        input=visual('attribute_age',bulk)
        input.setProperty('text','25,6')
        window.findChild(QObject,'onlyEmpty').setProperty('checked',False)
        click('prepareBulkButton');QTest.qWait(200)
        assert window.findChild(QObject,'bulkConfirm').property('visible')
        assert 'Изменится: 1' in backend.data['bulkSummary']
        assert result.features[0]['properties']['age']==12
        invoke('bulkConfirm','reject');QTest.qWait(600)
        assert result.features[0]['properties']['age']==12
        click('prepareBulkButton');QTest.qWait(200)
        assert window.findChild(QObject,'bulkConfirm').property('visible'), (backend.data['status'],backend.data['tableSelected'])
        assert backend.pending_bulk is not None
        invoke('bulkConfirm','accept');QTest.qWait(250)
        assert result.features[0]['properties']['age']==25.6, (backend.data['status'],backend.data['bulkSummary'],backend.data['error'], backend.pending_bulk,backend.result is result)
        assert result.features[1]['properties']['age']==2
        path=tmp_path/'bulk.geotrace';backend.saveProject(QUrl.fromLocalFile(str(path)));wait()
        assert load_project(path).result.features[0]['properties']['age']==25.6
        click('tableUndoButton')
        assert result.features[0]['properties']['age']==12
        assert backend.data['tableSelected']==0
        cell=visual('tableCell_'+str(result.features[0]['properties']['object_id'])+'_2')
        assert cell is not None
        QTest.mouseDClick(window,Qt.LeftButton,Qt.NoModifier,cell.mapToScene(QPointF(cell.width()/2,cell.height()/2)).toPoint())
        QTest.qWait(300)
        assert not window.findChild(QObject,'polygonDialog').property('visible')
        assert backend.selected==0
        backend.table.choose_filtered(True)
        assert backend.prepareBulk('age','9',False)
        backend.updateAttributes('X','Изменено после предпросмотра','')
        backend.applyBulk()
        assert backend.data['error'] and result.features[0]['properties']['age']==12
        assert not warnings,warnings
    finally:
        backend.timer.stop();backend.autosave_timer.stop()
        window.setProperty('closingConfirmed',True);window.close()
        del window;del engine
        backend.pool.shutdown(wait=True,cancel_futures=True);backend.temp.cleanup()
