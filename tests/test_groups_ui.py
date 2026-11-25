import time
from pathlib import Path

from PySide6.QtCore import QObject,QPointF,QMetaObject,Qt,QUrl
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from shapely.geometry import Point

from geotrace.document import load_document
from geotrace.spatial import project_geometry
from test_workspace_ux import scene


def wait(b):
    for _ in range(600):
        QTest.qWait(20);time.sleep(.002)
        if not b.data['busy']:break
    assert not b.data['busy'] and not b.data['error'],b.data['status']


def click(w,item):
    assert item.isVisible()
    p=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
    QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,p);QTest.qWait(40)


def test_group_manager_create_assign_delete_undo_and_save(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        before={f['properties']['object_id']:f['geometry'].wkb for f in b.result.features}
        panel=w.findChild(QObject,'workspaceClassManager');QMetaObject.invokeMethod(panel,'open');QTest.qWait(80)
        click(w,w.findChild(QQuickItem,'newProjectGroup'))
        w.findChild(QObject,'projectGroupName').setProperty('text','Custom unit')
        w.findChild(QObject,'projectGroupColor').setProperty('text','#cc8844')
        click(w,w.findChild(QQuickItem,'saveProjectGroup'));wait(b)
        assert len(b.document.classes)==5 and b.document.classes[-1]['name']=='Custom unit'
        QMetaObject.invokeMethod(panel,'close');QTest.qWait(80)
        b.set_edit_selection([0]);w.setProperty('attributesPinned',True);QTest.qWait(80)
        oid=b.result.features[0]['properties']['object_id']
        choice=w.findChild(QQuickItem,'objectGroupChoice');click(w,choice)
        QTest.keyClick(w,Qt.Key_End);QTest.keyClick(w,Qt.Key_Return);wait(b)
        assert next(f for f in b.result.features if f['properties']['object_id']==oid)['properties']['class_id']==5
        b.undo();QTest.qWait(40)
        assert next(f for f in b.result.features if f['properties']['object_id']==oid)['properties']['class_id']!=5
        b.redo();QTest.qWait(40)
        assert next(f for f in b.result.features if f['properties']['object_id']==oid)['properties']['class_id']==5
        assert b.deleteGroup(5,1);wait(b)
        assert len(b.document.classes)==4
        assert {f['properties']['object_id']:f['geometry'].wkb for f in b.result.features}==before
        path=tmp_path/'groups.geotrace';b.saveProject(QUrl.fromLocalFile(str(path)));wait(b)
        restored=load_document(path)
        assert len(restored.classes)==4 and len(restored.manual.features)==len(before)
        assert not warnings,warnings


def test_polygon_selection_stroke_stays_thin_at_large_zoom(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        index=next(i for i,f in enumerate(b.result.features) if f['properties']['source_sheet']=='a' and f['properties']['source_group']==1)
        b.set_edit_selection([index])
        world=project_geometry(Point(56.04,59.97),'EPSG:4326')
        x,y=(~b.raster.transform)*(world.x,world.y)
        viewport=w.findChild(QQuickItem,'mapViewport');canvas=w.findChild(QQuickItem,'rasterCanvas')
        widths=[]
        for zoom in (1.,8.,20.):
            viewport.setProperty('zoom',zoom)
            viewport.setProperty('frameX',viewport.width()/2-(x+float(viewport.property('paddingX')))*zoom)
            viewport.setProperty('frameY',viewport.height()/2-(y+float(viewport.property('paddingY')))*zoom)
            viewport.setProperty('contentX',0);viewport.setProperty('contentY',0)
            QTest.qWait(150);time.sleep(.005);QTest.qWait(50)
            assert any(p['selected'] for p in b.data['polygonPaths'])
            pos=canvas.mapToScene(QPointF(x*zoom,y*zoom))
            image=w.grabWindow();ratio=image.devicePixelRatio();sx,sy=round(pos.x()*ratio),round(pos.y()*ratio)
            count=0
            for px in range(sx-20,sx+21):
                color=image.pixelColor(px,sy)
                count+=color.blue()>190 and color.red()<80 and color.green()<160
            widths.append(count)
        assert min(widths)>=1 and max(widths)<=4 and max(widths)-min(widths)<=2,widths
        assert not warnings,warnings


def test_hidden_group_cannot_be_accidentally_selected(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        feature=b.result.features[0];cid=feature['properties']['class_id'];point=feature['geometry'].representative_point()
        b.setGroupVisible(cid,False);wait(b)
        x,y=(~b.raster.transform)*(point.x,point.y);b.selectAt(x,y)
        assert not b.data['selection']
        assert cid in b.data['hiddenClassIds']
        assert len(b.result.features)==4


def test_seam_display_switch_undo_preserves_objects(tmp_path):
    with scene(tmp_path) as (b,w,warnings):
        original=[(f['properties'].copy(),f['geometry'].wkb) for f in b.result.features]
        panel=w.findChild(QObject,'workspaceSheetsDialog');QMetaObject.invokeMethod(panel,'open');QTest.qWait(80)
        control=w.findChild(QQuickItem,'smoothScanSeams');click(w,control);wait(b)
        assert b.document.settings['smooth_scan_seams'] is False
        assert [(f['properties'],f['geometry'].wkb) for f in b.result.features]==original
        QMetaObject.invokeMethod(panel,'close');QTest.qWait(50)
        b.undo();assert b.data['smoothScanSeams'] is True
        b.redo();assert b.data['smoothScanSeams'] is False
        target=tmp_path/'seams.geotrace';b.saveProject(QUrl.fromLocalFile(str(target)));wait(b)
        assert load_document(target).settings['smooth_scan_seams'] is False
        assert not warnings
