import pytest
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('QT_QUICK_BACKEND','software')
from pathlib import Path
import numpy as np
import rasterio
from PySide6.QtCore import QObject,QPoint,QPointF,QUrl,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest, QSignalSpy
from geotrace import app as module
from geotrace.app import Backend
from geotrace.project import load_project
from test_core import sample


def test_preparation_palette_selection_merge_cancel_apply_save_and_undo(tmp_path):
    QQuickStyle.setStyle('Basic');application=QGuiApplication.instance() or QGuiApplication([])
    b=Backend(recovery_dir=tmp_path/'recovery');b.autosave_timer.stop()
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(x.toString() for x in items))
    engine.rootContext().setContextProperty('backend',b)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent/'qml/Main.qml')))
    assert engine.rootObjects(),warnings
    w=engine.rootObjects()[0]
    def visual(name,item=None):
        item=item or w.contentItem()
        if item.objectName()==name:return item
        for child in item.childItems():
            found=visual(name,child)
            if found is not None:return found
    def click(name):
        item=visual(name)
        assert item is not None and item.isVisible() and item.isEnabled(),name
        QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint());QTest.qWait(150)
    def wait():
        for _ in range(300):
            QTest.qWait(50)
            if not b.data['busy']:break
        assert not b.data['busy'] and not b.data['error'],b.data['status']
        QTest.qWait(150)
    try:
        r=sample();path=tmp_path/'map.tif'
        with rasterio.open(path,'w',driver='GTiff',width=100,height=60,count=4,dtype='uint8',crs=r.crs_wkt,transform=r.transform) as dst:
            dst.write(np.moveaxis(r.rgb,-1,0),indexes=[1,2,3]);dst.write(r.valid.astype('uint8')*255,4)
            dst.colorinterp=(rasterio.enums.ColorInterp.red,rasterio.enums.ColorInterp.green,rasterio.enums.ColorInterp.blue,rasterio.enums.ColorInterp.alpha)
        b.openFile(QUrl.fromLocalFile(str(path)));wait()
        assert b.data['qualityReady'] and len(b.data['qualityPalette'])==2
        dialog=w.findChild(QObject,'paletteDialog');assert dialog.property('visible')
        assert w.findChild(QObject,'autoProfile').property('currentIndex')==2
        view=visual('paletteViewport');layer=visual('paletteMapLayer')
        wheel_pos=view.mapToScene(QPointF(view.width()*.43,view.height()*.52))
        before_wheel=layer.mapFromScene(wheel_pos)/layer.width()
        wheel=QWheelEvent(wheel_pos,QPointF(w.mapToGlobal(wheel_pos.toPoint())),QPoint(),
                          QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        QGuiApplication.sendEvent(w,wheel)
        assert view.property('zoom')==1.25
        after_wheel=layer.mapFromScene(wheel_pos)/layer.width()
        assert after_wheel.x()==pytest.approx(before_wheel.x(),abs=.002)
        click('paletteFit')
        click('paletteZoomIn');click('paletteZoomIn')
        assert view.property('zoom')==2.25
        click('sampleGroupButton')
        samples=QSignalSpy(view.sampled)
        start=view.mapToScene(QPointF(view.width()/2,view.height()/2)).toPoint()
        end=start+QPointF(58,34).toPoint()
        before=layer.position()
        QTest.mousePress(w,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(w,end,25)
        QTest.mouseRelease(w,Qt.LeftButton,Qt.NoModifier,end)
        assert samples.count()==0 and layer.x()-before.x()==pytest.approx(58)
        assert dialog.property('picking')
        point=layer.mapFromScene(QPointF(start))
        expected=[int(point.x()*r.rgb.shape[1]/layer.width()),int(point.y()*r.rgb.shape[0]/layer.height())]
        QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,start)
        assert samples.count()==1 and list(samples.at(0))==expected
        assert b.sample_lab is not None and not isinstance(b.sample_lab,np.memmap)
        lab_file=Path(b.temp.name)/'palette-lab.npy';moved=lab_file.with_suffix('.bak')
        lab_file.replace(moved);moved.replace(lab_file)  # No live file mapping may block reuse on Windows.
        click('paletteFit')
        assert view.property('zoom')==1 and view.property('centerX')==.5
        click('paletteFocus_2');assert b.data['paletteUrl']
        w.findChild(QObject,'paletteMergeTarget').setProperty('currentIndex',0)
        click('mergePaletteButton');assert b.quality_groups==[1,1]
        click('resetPaletteButton');assert b.quality_groups==[1,2]
        click('paletteInclude_2');assert b.quality_groups==[1,0]
        click('paletteRunButton');wait()
        assert b.result is None and b.data['autoReady']
        assert len(b.auto_preview.features)==1
        click('autoAcceptButton');assert b.result is not None
        old=b.result
        target=tmp_path/'selected.geotrace';b.saveProject(QUrl.fromLocalFile(str(target)));wait()
        loaded=load_project(target).result
        assert len(loaded.features)==1
        np.testing.assert_array_equal(old.labels,loaded.labels)
        # Cooperative cancellation cannot replace accepted data, even if the
        # computation completes before the UI polls its Future.
        b.previewQuality();b.cancelQuality();wait()
        assert b.result is old and not b.data['autoReady'] and 'остановлен' in b.data['status']
        b.previewQuality();wait();b.acceptAuto();assert b.result is not old
        b.undo();assert b.result is old
        b.undo();assert b.result is None
        # A new map receives a fresh token and resets excluded/merged groups.
        previous=b.quality_token;b.openFile(QUrl.fromLocalFile(str(path)));wait()
        assert b.quality_token!=previous and b.quality_groups==[1,2]
        assert not warnings,warnings
    finally:
        b.timer.stop();b.autosave_timer.stop()
        w.setProperty('closingConfirmed',True);w.close()
        b.pool.shutdown(wait=True,cancel_futures=True);del engine;b.temp.cleanup()


def test_review_navigation_preserves_last_site_and_decisions(tmp_path):
    app=QGuiApplication.instance() or QGuiApplication([])
    backend=Backend(recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    try:
        backend.review_sites=[dict(reason='Слабая граница',tool=2,bounds=[i,0,i+20,20],
                                   marker=dict(x=i,y=0)) for i in range(2)]
        backend.nextReviewSite()
        backend.confirmReviewSite()
        assert backend.data['reviewPosition']==2
        backend.confirmReviewSite()
        backend.nextReviewSite()
        assert backend.data['reviewCount']==2 and backend.data['reviewPosition']==2
        backend.previousReviewSite()
        assert backend.data['reviewDecision']=='Принят без правки'
        assert backend.data['reviewMarker']['x']==0
        backend.nextReviewSite()
        assert backend.review_sites[0]['decision']=='Принят без правки'
        backend.reset_review()
        assert backend.data['reviewPosition']==0 and not backend.data['reviewMarker']
    finally:
        backend.timer.stop()
        backend.pool.shutdown()
        backend.temp.cleanup()
