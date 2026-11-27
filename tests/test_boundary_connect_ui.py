from pathlib import Path
import time
from PySide6.QtCore import Qt,QPointF
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from shapely.geometry import box
from geotrace.editing import transform_geometry,_result_from_features
from geotrace.document import save_document,load_document
from test_workspace_ux import scene


def wait(b):
 for _ in range(1200):
  QTest.qWait(15);time.sleep(.001)
  if not b.data['busy']:break
 assert not b.data['busy'] and not b.data['error'],b.data['status']


def setup(b):
 features=[]
 for i,g in enumerate([box(10,10,40,50),box(45,10,75,50)]):
  props=dict(b.result.features[i]['properties']);props.update(object_id=i+1,note='Сохранить атрибут')
  features.append(dict(geometry=transform_geometry(g,b.raster.transform),properties=props))
 b.set_result(_result_from_features(b.raster,features,b.result.colors,b.result.fields));b.setEditTool(5)
 return [f['geometry'] for f in b.result.features]


def test_two_click_ui_preview_anchor_change_apply_undo_and_roundtrip(tmp_path):
 with scene(tmp_path) as (b,w,warnings):
  before=setup(b);w.setProperty('viewing',False)
  # Tool selection, as from the keyboard, places the map in polygon editing.
  from PySide6.QtCore import QMetaObject,Q_ARG
  QMetaObject.invokeMethod(w,'chooseTool',Qt.DirectConnection,Q_ARG('QVariant',5),Q_ARG('QVariant',False))
  b.setBoundaryMode('span');QTest.qWait(80)
  canvas=w.findChild(QQuickItem,'rasterCanvas');viewport=w.findChild(QQuickItem,'mapViewport');zoom=float(viewport.property('zoom'))
  def point(x,y):return canvas.mapToScene(QPointF(x*zoom,y*zoom)).toPoint()
  QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,point(40,17));QTest.qWait(40)
  assert b.data['connectionStage']==1 and len(b.data['connectionPoints'])==1
  QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,point(40,39));wait(b)
  assert b.data['geometryReady'] and b.data['connectionStage']==2
  assert b.data['connectionLive']['valid'] and len(b.data['connectionLive']['objects'])==2
  assert [f['geometry'] for f in b.result.features]==before
  b.previewBoundarySpan('source');wait(b);b.acceptGeometry();QTest.qWait(60)
  assert not b.data['geometryReady'] and b.data['connectionStage']==0
  assert b.result.features[0]['geometry'].hausdorff_distance(before[0])<1e-7
  assert b.result.features[0]['geometry'].boundary.intersection(b.result.features[1]['geometry'].boundary).length>1
  assert all(f['properties']['note']=='Сохранить атрибут' for f in b.result.features)
  path=tmp_path/'connected.geotrace';save_document(path,b.document,b.raster,b.result);loaded=load_document(path,tmp_path/'sources')
  assert loaded.manual.features[0]['geometry'].equals_exact(b.result.features[0]['geometry'],0)
  b.undo();assert all(f['geometry'].equals_exact(g,0) for f,g in zip(b.result.features,before))
  b.redo();assert b.result.features[0]['geometry'].boundary.intersection(b.result.features[1]['geometry'].boundary).length>1
  assert not warnings,warnings


def test_connection_drag_live_preview_and_final_drop_and_cancel(tmp_path):
 with scene(tmp_path) as (b,w,warnings):
  before=setup(b)
  from PySide6.QtCore import QMetaObject,Q_ARG
  QMetaObject.invokeMethod(w,'chooseTool',Qt.DirectConnection,Q_ARG('QVariant',5),Q_ARG('QVariant',False));QTest.qWait(60)
  b.setBoundaryMode('connect');b.beginEdgeDrag(40,27,1,False)
  b.previewBoundaryConnection(4.5,0,1)
  for _ in range(500):
   QTest.qWait(15)
   if b.data['connectionLive']:break
  assert b.data['connectionLive']['valid'] and b.data['connectionLive']['snapped']
  b.finishEdgeDrag(10,0);b.cancelEdgeDrag();wait(b)
  assert b.result.features[0]['geometry'].boundary.intersection(b.result.features[1]['geometry'].boundary).length>1
  assert not b.data['connectionLive'];b.undo()
  assert all(f['geometry'].equals_exact(g,0) for f,g in zip(b.result.features,before))
  b.setBoundaryMode('span');b.connectBoundaryClick(40,17,2);b.clearBoundaryConnection()
  assert b.data['connectionStage']==0 and not b.data['geometryReady']
  b.setWorkspaceLayer('polygonsLocked',True);b.connectBoundaryClick(40,17,2)
  assert b.data['connectionStage']==0 and [f['geometry'] for f in b.result.features]==before
