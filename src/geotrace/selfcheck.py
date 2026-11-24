"""Exercise the frozen distribution using temporary data and its real workers."""
import json
from pathlib import Path
import time

import numpy as np
import rasterio
from affine import Affine
import fiona
from PySide6.QtCore import QTimer,QUrl,QObject

from .cartography import layout_defaults
from .document import load_document


def start(backend,engine,report,application,folder):
    b=backend;folder=Path(folder);report=Path(report);report.parent.mkdir(parents=True,exist_ok=True)
    b.autosave_timer.stop()
    files=[]
    for i in range(2):
        path=folder/f'sheet-{i}.tif';rgb=np.empty((100,140,3),np.uint8)
        rgb[:,:70]=[220,185,115] if i==0 else [165,155,205];rgb[:,70:]=[150,215,225]
        with rasterio.open(path,'w',driver='GTiff',width=140,height=100,count=3,dtype='uint8',crs='EPSG:4326',transform=Affine(.001,0,56+i*.13,0,-.001,60)) as dst:dst.write(np.moveaxis(rgb,-1,0))
        files.append(QUrl.fromLocalFile(str(path)))
    state=dict(step=0,started=time.monotonic(),window=engine.rootObjects()[0]);timer=QTimer(application);timer.setInterval(50)
    target=folder/'workspace.geotrace';gpkg=folder/'workspace.gpkg';pdf=folder/'layout.pdf'
    def finish(error=None):
        timer.stop()
        data=dict(ok=error is None,seconds=round(time.monotonic()-state['started'],3),error=error,
            version=__import__('geotrace').__version__,build=__import__('geotrace.release_info',fromlist=['read_build_info']).read_build_info(),
            sheets=len(b.document.sheets),polygons=len(b.result.features) if b.result else 0,lines=len(b.document.lines),
            display_crs=b.raster.crs_label if b.raster else '',gdal=rasterio.__gdal_version__,fiona_gdal=fiona.__gdal_version__,
            geopackage=gpkg.is_file(),pdf=pdf.is_file())
        data['browser_renderer']=bool(state.get('browser_ready'))
        if not error:
            engine.rootObjects()[0].grabWindow().save(str(report.with_suffix('.png')))
        report.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        engine.rootObjects()[0].setProperty('closingConfirmed',True)
        application.exit(0 if error is None else 5)
    def tick():
        if time.monotonic()-state['started']>150:
            finish('Self-check timeout at step '+str(state['step']));return
        if b.data['busy']:return
        if b.data['error']:
            finish(b.data['status']);return
        try:
            step=state['step']
            if step==0:b.addSheets(files)
            elif step==1:
                assert len(b.document.sheets)==2 and b.raster.crs_label=='EPSG:3857'
                b.digitiseAll()
            elif step==2:
                assert b.data['autoReady'];b.acceptAuto();assert len(b.result.features)>0
                assert b.saveLineType(json.dumps(dict(name='Разлом',colors=['#b53239'],style={})))
                b.setLineMode(True);b.setLineTool('draw');b.lineClick(300,200,8);b.lineClick(500,300,8);b.finishLine()
                assert len(b.document.lines)==1
                b.savePortable(QUrl.fromLocalFile(str(target)))
            elif step==3:
                doc=load_document(target,folder/'read-sources');assert len(doc.sheets)==2 and len(doc.lines)==1
                b.openFile(QUrl.fromLocalFile(str(target)))
            elif step==4:
                assert len(b.document.lines)==1;b.exportFile(QUrl.fromLocalFile(str(gpkg)))
            elif step==5:
                assert set(fiona.listlayers(gpkg))=={'polygons','lines'}
                options=b.defaultLayout();options.update(dpi=72,raster=True)
                state['layout']=options;b.previewLayout(json.dumps(options))
            elif step==6:
                assert b.data['layoutPreviewUrl'];b.exportLayout(QUrl.fromLocalFile(str(pdf)),json.dumps(state['layout']))
            elif step==7:
                assert pdf.read_bytes().startswith(b'%PDF')
                b.web_map.document=b'<!doctype html><html><head><title>GeoTrace offline renderer ready</title></head><body>GeoTrace offline browser check</body></html>'
                b.openWebMap()
                engine.rootObjects()[0].findChild(QObject,'workspaceTabs').setProperty('currentIndex',1)
            elif step==8:
                browser=engine.rootObjects()[0].findChild(QObject,'webMapBrowser')
                if browser is None or browser.property('title')!='GeoTrace offline renderer ready':return
                state['browser_ready']=True
                engine.rootObjects()[0].findChild(QObject,'workspaceTabs').setProperty('currentIndex',0)
            else:
                finish();return
            state['step']+=1
        except Exception as exc:
            import traceback
            finish('Step '+str(state['step'])+' · '+traceback.format_exc())
    timer.timeout.connect(tick);timer.start()
    return timer
