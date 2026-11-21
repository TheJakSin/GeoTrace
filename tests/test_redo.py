from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from shapely.geometry import box

from geotrace.app import Backend,edit_job,area_job
from geotrace.editing import transform_geometry
from test_editing import baseline


def setup_backend(tmp_path):
    application=QGuiApplication.instance() or QGuiApplication([])
    backend=Backend(settings=QSettings(str(tmp_path/'test.ini'),QSettings.IniFormat),recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    original=baseline();backend.kind='load';backend.future=Future();backend.future.set_result(original.raster);backend.poll()
    backend.set_result(original)
    return application,backend,original


def close_backend(backend):
    backend.timer.stop();backend.autosave_timer.stop();backend.web_map.close();backend.basemap_loader.close()
    backend.pool.shutdown(wait=True,cancel_futures=True);backend.temp.cleanup()


def same(actual,expected):
    assert len(actual.features)==len(expected.features)
    for a,b in zip(actual.features,expected.features):
        assert a['geometry'].equals_exact(b['geometry'],0)
        assert a['properties']==b['properties']


def test_geometry_redo_and_attributes_multiple_steps_and_new_branch(tmp_path):
    application,b,original=setup_backend(tmp_path)
    try:
        ids=[f['properties']['object_id'] for f in original.features]
        b.geometry_operation='merge';b.geometry_primary_id=ids[0]
        b.edit_preview=edit_job(original,ids,'merge',[],b.temp.name,original.raster)
        b.commit_geometry();merged=b.result
        b.selectAt(75,35);b.saveAttributes('Повторяемое значение','{}')
        b.undo();assert b.data['canRedo'] and b.data['selection']['note']==''
        b.undo();same(b.result,original)
        b.redo();same(b.result,merged);assert b.data['canRedo']
        b.redo();assert b.result.features[0]['properties']['note']=='Повторяемое значение'
        assert b.data['selection']['note']=='Повторяемое значение' and not b.data['canRedo']
        b.undo();b.selectAt(75,35);b.saveAttributes('Новая ветка','{}')
        assert not b.redo_stack and not b.data['canRedo']
        b.redo();assert b.result.features[0]['properties']['note']=='Новая ветка'
    finally:close_backend(b)


def test_crop_redo_restores_mask_and_exact_geometry_then_hint_invalidates_redo(tmp_path):
    import numpy as np
    application,b,original=setup_backend(tmp_path)
    try:
        geometry=transform_geometry(box(5,5,90,50),b.raster.transform)
        b.area_snapshot=(b.raster,b.result,list(b.points),b.roi)
        cropped=area_job(b.raster,b.result,geometry,'Участок',b.temp.name)
        b.kind='area_apply';b.future=Future();b.future.set_result(cropped);b.poll()
        b.undo();same(b.result,original);assert b.raster is original.raster
        b.redo();same(b.result,cropped[1]);assert np.array_equal(b.raster.valid,cropped[0].valid)
        b.undo();same(b.result,original)
        b.addHint(10,10,1,512)
        assert not b.redo_stack and not b.data['canRedo']
    finally:close_backend(b)
