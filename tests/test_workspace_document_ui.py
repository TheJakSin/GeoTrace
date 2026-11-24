import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

import json
from pathlib import Path
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from geotrace.document import load_document
from geotrace.workspace_app import WorkspaceBackend, build_workspace
from test_document import two_sheets


def test_area_workspace_attributes_lines_undo_and_roundtrip(tmp_path):
    app = QGuiApplication.instance() or QGuiApplication([])
    backend = WorkspaceBackend(settings=QSettings(str(tmp_path/'settings.ini'), QSettings.IniFormat), recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    try:
        backend._install(build_workspace(two_sheets(), backend.temp.name))
        assert backend.data['workspaceEnabled'] and len(backend.data['sheets']) == 2
        assert backend.raster.crs_label == 'EPSG:3857'
        assert backend.data['summary']['area_m2'] > 0
        assert backend.saveLineType(json.dumps(dict(name='Разлом', colors=['#b53239'], style={})))
        backend.setLineMode(True)
        backend.setLineTool('draw')
        backend.lineClick(300, 200, 8)
        backend.lineClick(600, 300, 8)
        backend.finishLine()
        assert len(backend.document.lines) == 1 and backend.data['lineSelection']['length_m'] > 0
        assert backend.applyLineAttributes(json.dumps(dict(name='F1', note='Проверен')))
        assert backend.document.lines[0]['properties']['name'] == 'F1'
        backend.undo()
        assert backend.document.lines[0]['properties']['name'] == ''
        backend.redo()
        assert backend.document.lines[0]['properties']['name'] == 'F1'
        target = tmp_path/'workspace.geotrace'
        backend.saveProject(QUrl.fromLocalFile(str(target)))
        for _ in range(300):
            QTest.qWait(20)
            if not backend.data['busy']:
                break
        assert not backend.data['busy'] and not backend.data['error'], backend.data['status']
        doc = load_document(target)
        assert len(doc.sheets) == 2 and doc.lines[0]['properties']['name'] == 'F1'
        assert not backend.data['dirty']
        # A full-sheet line can cross KML. Unchanged outside portions must not
        # prevent editing the portion inside; new outside movement is rejected.
        from shapely.geometry import LineString,box
        from geotrace.region import set_area
        x0,y0,x1,y1=backend.raster.area.bounds
        width,height=x1-x0,y1-y0;mid=(y0+y1)/2
        backend.raster=set_area(backend.raster,box(x0+width*.25,y0,x0+width*.75,y1),'Участок')
        original=LineString([(x0+width*.1,mid),(x0+width*.9,mid)])
        changed=LineString([(x0+width*.1,mid),(x0+width*.3,mid),(x0+width*.5,mid+height*.05),(x0+width*.7,mid),(x0+width*.9,mid)])
        assert backend.line_geometry_allowed(changed,[original])
        assert not backend.line_geometry_allowed(LineString([(x0+width*.1,mid+height*.1),(x0+width*.9,mid)]),[original])
    finally:
        backend.timer.stop()
        backend.basemap_loader.close()
        backend.web_map.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        backend.close_details()
        backend.temp.cleanup()
