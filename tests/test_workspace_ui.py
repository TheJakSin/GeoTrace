import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
from concurrent.futures import Future
from pathlib import Path
from PySide6.QtCore import QObject,QSettings,QPointF,QUrl,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shapely.geometry import box
from geotrace import app as module
from geotrace.app import Backend
from geotrace.core import save_preview,save_overlay
from geotrace.attributes import change_schema
from test_draw_vertex import objects


def test_workspace_groups_shift_panels_review_and_palette_visibility(tmp_path):
    QQuickStyle.setStyle('Basic');application=QGuiApplication.instance() or QGuiApplication([])
    b=Backend(settings=QSettings(str(tmp_path/'test.ini'),QSettings.IniFormat),recovery_dir=tmp_path/'recovery');b.autosave_timer.stop()
    engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda items:warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty('backend',b)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent/'qml/Main.qml')));window=engine.rootObjects()[0]
    def visual(name,parent):
        if parent.objectName()==name:return parent
        for child in parent.childItems():
            found=visual(name,child)
            if found is not None:return found
    def item(name):
        found=window.findChild(QQuickItem,name)
        return found if found is not None else visual(name,window.contentItem())
    def center(name):
        obj=item(name);assert obj is not None and obj.isVisible() and obj.isEnabled(),name
        return obj.mapToScene(QPointF(obj.width()/2,obj.height()/2)).toPoint()
    def click(name):QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(name));QTest.qWait(70)
    def wait():
        for _ in range(300):
            QTest.qWait(30)
            if not b.data['busy']:break
        assert not b.data['busy'] and not b.data['error'],b.data['status']
        QTest.qWait(80)
    try:
        result=objects([box(0,0,15,15),box(15,0,30,15),box(40,0,55,15)])
        result.colors.append([200,150,100]);result.features[2]['properties']['class_id']=2
        result=change_schema(result,[dict(key='checked',label='Готово',type='boolean',required=False,default=None,choices=[])])
        result.features[0]['properties'].update(note='А',checked=False);result.features[1]['properties'].update(note='Б',checked=True)
        save_preview(result.raster,Path(b.temp.name)/'raster.png')
        b.kind='load';b.future=Future();b.future.set_result(result.raster);b.poll()
        save_overlay(result,Path(b.temp.name)/'overlay.png');b.set_result(result);QTest.qWait(100)
        assert not item('attributesPanel').isVisible() and not item('toolsSidebar').isVisible()
        view=item('mapViewport');before=view.width()
        canvas=item('rasterCanvas')
        def camera():
            anchor=canvas.mapToScene(QPointF(20*view.property('zoom'),20*view.property('zoom')))
            return (view.property('zoom'),anchor.x(),anchor.y(),view.width(),view.height())
        def unchanged(snapshot):
            QTest.qWait(90)
            assert all(abs(a-b)<.01 for a,b in zip(camera(),snapshot)), (snapshot,camera())
        snapshot=camera()
        # The initial tool is read-only, even on a polygon and a shared border.
        assert window.property('viewing')
        for x in (5,15):
            pos=canvas.mapToScene(QPointF(x*view.property('zoom'),10*view.property('zoom'))).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,pos)
        assert not b.edit_selected and b.selected==-1 and b.edge_drag is None
        unchanged(snapshot)
        click('layersButton');assert item('toolsSidebar').isVisible() and view.width()==before;unchanged(snapshot)
        click('closeLayers');assert view.width()==before;unchanged(snapshot)
        for index,tool in [(1,0),(2,5),(3,1),(4,3),(5,4),(0,0)]:
            click('toolButton_'+str(index));assert b.edit_tool==tool
            assert not item('toolsSidebar').isVisible()
        snapshot=camera()
        b.selectForEdit(5,5);b.selectForEdit(20,5);QTest.qWait(70)
        unchanged(snapshot)
        # Context actions may appear and disappear at fit and at a manual zoom.
        b.clearEditSelection();unchanged(snapshot)
        view.setProperty('zoom',view.property('zoom')*1.5);view.setProperty('contentX',80);view.setProperty('contentY',40);QTest.qWait(30)
        snapshot=camera();b.selectForEdit(5,5);b.selectForEdit(20,5);unchanged(snapshot)
        click('attributesToggle');unchanged(snapshot);click('closeAttributes');unchanged(snapshot)
        click('viewToolButton');assert window.property('viewing');unchanged(snapshot)
        QTest.keyClick(window,Qt.Key_V);QTest.qWait(30);assert not window.property('viewing') and b.edit_tool==0
        QTest.keyClick(window,Qt.Key_L);assert item('lassoToggle').property('checked')
        QTest.keyClick(window,Qt.Key_B);assert b.edit_tool==5
        QTest.keyClick(window,Qt.Key_Q);assert window.property('viewing')
        QTest.keyClick(window,Qt.Key_V)
        from PySide6.QtCore import QCoreApplication,QEvent
        from PySide6.QtGui import QKeyEvent
        # The Russian physical-key equivalent of Q is Й.
        for kind in (QEvent.KeyPress,QEvent.KeyRelease):
            QCoreApplication.sendEvent(window,QKeyEvent(kind,ord('Й'),Qt.NoModifier,'й'))
        assert window.property('viewing')
        QTest.keyClick(window,Qt.Key_V)
        # F1 and the application button open the same concise modal help.
        QTest.keyClick(window,Qt.Key_F1);QTest.qWait(50)
        help_dialog=window.findChild(QObject,'helpDialog');assert help_dialog.property('visible')
        QTest.keyClick(window,Qt.Key_P);assert b.edit_tool==0, 'Modal help must block map actions.'
        QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(250);assert not help_dialog.property('visible')
        click('helpButton');assert help_dialog.property('visible')
        QMetaObject.invokeMethod(help_dialog,'close',Qt.DirectConnection);QTest.qWait(250)
        unchanged(snapshot)
        # View also pans with the left mouse button without changing selection.
        QTest.keyClick(window,Qt.Key_Q);QTest.qWait(40)
        indices=list(b.edit_selected);before_pan=view.property('contentX')
        origin=canvas.mapToScene(QPointF(40*view.property('zoom'),30*view.property('zoom'))).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,origin)
        QTest.mouseMove(window,origin-QPointF(25,0).toPoint(),25)
        QTest.mouseMove(window,origin-QPointF(80,0).toPoint(),25)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,origin-QPointF(80,0).toPoint());QTest.qWait(150)
        assert view.property('contentX')>before_pan and b.edit_selected==indices
        QTest.keyClick(window,Qt.Key_V)
        button=item('toolButton_0');QTest.mouseMove(window,center('toolButton_0'));QTest.qWait(750)
        def visible_texts(parent):
            result=[parent.property('text')] if parent.isVisible() and parent.property('text') else []
            for child in parent.childItems():result.extend(visible_texts(child))
            return result
        assert button.property('description')+' · V' in visible_texts(window.contentItem())
        assert not item('attributesPanel').isVisible(), 'Geometry selection must not open attributes.'
        original_view=b.view.copy();revision=b.edit_revision
        click('compareOriginalButton');assert window.property('originalOnly') and not item('polygonOverlay').isVisible()
        indices=list(b.edit_selected);canvas=item('rasterCanvas')
        source_point=canvas.mapToScene(QPointF(canvas.width()*.05,canvas.height()*.05)).toPoint()
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,source_point)
        assert b.edit_selected==indices and b.edit_revision==revision, 'Source comparison is an inspection mode.'

        QTest.keyClick(window,Qt.Key_Shift);QTest.qWait(30)
        assert not window.property('originalOnly') and item('polygonOverlay').isVisible()
        QTest.keyClick(window,Qt.Key_Shift);assert window.property('originalOnly')
        QTest.keyClick(window,Qt.Key_Shift);assert not window.property('originalOnly')
        assert b.view==original_view and b.edit_revision==revision
        # Shift used with a click must remain a modifier, not a compare toggle.
        QTest.keyPress(window,Qt.Key_Shift)
        QTest.mouseClick(window,Qt.LeftButton,Qt.ShiftModifier,center('toolButton_1'))
        QTest.keyRelease(window,Qt.Key_Shift);assert not window.property('originalOnly')
        click('attributesToggle');assert item('attributesPanel').isVisible()
        item('attributeTarget').setProperty('currentIndex',1);item('attributeGroup').setProperty('currentIndex',1);QTest.qWait(70)
        assert item('attributesPanel').property('scopeData')['count']==2
        assert item('noteField').property('placeholderText')=='Разные значения'
        choices=item('choice_checked');assert choices.property('currentIndex')==0
        # Merely choosing the mixed-state placeholder cannot clear existing values.
        from PySide6.QtCore import Q_ARG
        QMetaObject.invokeMethod(choices,'activated',Qt.DirectConnection,Q_ARG(int,0))
        click('scopeOnlyEmpty')
        field=item('noteField');field.forceActiveFocus();QTest.keyClick(window,Qt.Key_Shift)
        selected_tool=b.edit_tool
        
        for key in (Qt.Key_V,Qt.Key_B,Qt.Key_G,Qt.Key_A,Qt.Key_P):QTest.keyClick(window,key)
        assert b.edit_tool==selected_tool and field.property('text').endswith('vbgap')
        assert not window.property('originalOnly'), 'Typing Shift cannot compare the map.'
        field.setProperty('text','Общая группа');b.notify(status='Не связан с атрибутами');QTest.qWait(30)
        assert field.property('text')=='Общая группа' and item('attributeGroup').property('currentIndex')==1
        click('closeAttributes');click('attributesToggle')
        assert field.property('text')=='Общая группа', 'Closing the inspector must preserve its draft for the same scope.'
        field.forceActiveFocus();QTest.keyClick(window,Qt.Key_Return,Qt.ControlModifier);QTest.qWait(80)
        assert [f['properties']['note'] for f in b.result.features]==['Общая группа','Общая группа','']
        assert [f['properties']['checked'] for f in b.result.features]==[False,True,None]
        click('undoButton');assert [f['properties']['note'] for f in b.result.features]==['А','Б','']
        click('redoButton');assert b.result.features[0]['properties']['note']=='Общая группа'
        click('closeAttributes')
        b.notify(reviewCount=3,reviewPosition=1,reviewText='Проверка границы',reviewMarker=dict(x=1,y=1,w=10,h=10,url=b.url('overlay.png')))
        assert not item('reviewPanel').isVisible()
        snapshot=camera();click('reviewToggle');assert item('reviewPanel').isVisible() and item('reviewHighlight').isVisible();unchanged(snapshot)
        click('closeReview');assert not item('reviewPanel').isVisible() and not item('reviewHighlight').isVisible()
        assert b.data['reviewPosition']==1 and b.data['reviewCount']==3
        b.prepareQuality();wait();palette=window.findChild(QObject,'paletteDialog');assert palette.property('visible')
        click('paletteFocus_1');assert palette.property('focused')==1 and palette.property('colourPreview')
        click('paletteFocus_1');assert not palette.property('colourPreview')
        from PIL import Image
        import numpy as np
        assert not np.asarray(Image.open(Path(b.temp.name)/'palette-focus.png'))[:,:,3].any()
        groups=list(b.quality_groups);click('palettePreviewToggle')
        assert palette.property('colourPreview') and b.quality_groups==groups
        camera=item('paletteViewport');camera.setProperty('zoom',3.0);camera.setProperty('centerX',.65)
        QMetaObject.invokeMethod(palette,'close',Qt.DirectConnection);QMetaObject.invokeMethod(palette,'open',Qt.DirectConnection);QTest.qWait(60)
        assert camera.property('zoom')==3.0 and camera.property('centerX')==.65
        QMetaObject.invokeMethod(palette,'close',Qt.DirectConnection)
        window.setProperty('width',1100);window.setProperty('height',720);QTest.qWait(70)
        for name in ('attributesToggle','compareOriginalButton','openTableButton','toolButton_5'):
            obj=item(name);pos=obj.mapToScene(QPointF(0,0))
            assert pos.x()>=0 and pos.x()+obj.width()<=window.width()+1,name
        assert not warnings,warnings
    finally:
        b.timer.stop();b.autosave_timer.stop();window.setProperty('closingConfirmed',True);window.close();del window;del engine
        b.setCompareShortcutEnabled(False);b.web_map.close();b.basemap_loader.close();b.pool.shutdown(wait=True,cancel_futures=True);b.temp.cleanup()
