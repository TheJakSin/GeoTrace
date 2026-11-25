import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')

import json
from pathlib import Path

from PySide6.QtCore import QSettings,QUrl,QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from geotrace.workspace_app import WorkspaceBackend,build_workspace
from test_document import two_sheets


def test_layout_worker_view_detail_continuation_and_layer_lock(tmp_path):
    QQuickStyle.setStyle('Basic');app=QGuiApplication.instance() or QGuiApplication([])
    b=WorkspaceBackend(settings=QSettings(str(tmp_path/'test.ini'),QSettings.IniFormat),recovery_dir=tmp_path/'recovery')
    b.autosave_timer.stop();engine=QQmlApplicationEngine();warnings=[]
    engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
    engine.rootContext().setContextProperty('backend',b)
    engine.load(QUrl.fromLocalFile(str(Path(__import__('geotrace').__file__).parent/'qml/Main.qml')))
    assert engine.rootObjects(),warnings
    w=engine.rootObjects()[0]
    def wait(predicate):
        for _ in range(600):
            QTest.qWait(20)
            __import__('time').sleep(.002)
            if predicate():return
        raise AssertionError(b.data['status']+' · '+getattr(b,'detail_error',''))
    try:
        b._install(build_workspace(two_sheets(),b.temp.name),fit=True)
        b.requestDetail(0,0,b.data['width'],b.data['height'],500,300)
        wait(lambda:bool(b.data['detailUrl']))
        assert b.data['detailTiles'] and all(row['url'].startswith('file:') for row in b.data['detailTiles'])
        options=b.defaultLayout();options.update(raster=False,dpi=72)
        b.previewLayout(json.dumps(options));wait(lambda:not b.data['busy'])
        assert not b.data['error'],b.data['status']
        layout=w.findChild(QObject,'workspaceLayoutWindow');assert layout is not None
        layout.setProperty('draft',options);layout.setProperty('visible',True);QTest.qWait(200)
        layout.setProperty('arrange',True);QTest.qWait(80)
        assert b.data['layoutPreviewUrl']
        settings=w.findChild(QObject,'layoutSettings');preview=w.findChild(QObject,'layoutPreview')
        assert settings.property('width')<=335 and preview.property('width')>400
        target=tmp_path/'layout.pdf';b.exportLayout(QUrl.fromLocalFile(str(target)),json.dumps(options));wait(lambda:not b.data['busy'])
        assert not b.data['error'] and target.read_bytes().startswith(b'%PDF'),b.data['status']
        b.saveLineType(json.dumps(dict(name='F',colors=['#b53239'],style={})))
        b.setLineMode(True);b.setLineTool('draw');b.lineClick(100,100,8);b.lineClick(200,150,8);b.finishLine()
        assert len(b.document.lines)==1
        old=b.document.lines[0]['geometry'].length
        b.continueLine();b.lineClick(300,180,8);b.finishLine()
        assert len(b.document.lines)==1 and b.document.lines[0]['geometry'].length>old
        b.setWorkspaceLayer('polygonsLocked',True)
        count=len(b.document.fields)
        assert not b.saveField(json.dumps(dict(key='x',label='X',type='text')),'')
        assert len(b.document.fields)==count
        assert not warnings,warnings
    finally:
        w.setProperty('closingConfirmed',True);w.close()
        for root in engine.rootObjects():
            child=root.findChild(QObject,'workspaceLayoutWindow')
            if child:child.setProperty('visible',False)
        __import__('shiboken6').delete(engine)
        b.timer.stop();b.autosave_timer.stop();b.basemap_loader.close();b.web_map.close()
        b.pool.shutdown(wait=True,cancel_futures=True);b.close_details();b.temp.cleanup()
