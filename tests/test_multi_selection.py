import json
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from shapely.geometry import Polygon, box
from shapely.strtree import STRtree

from geotrace.app import Backend
from geotrace.editing import edit_geometry, lasso_indices
from geotrace.project import save_project, load_project
from test_draw_vertex import objects
from test_editing import assert_coverage


def test_bulk_merge_components_attributes_holes_and_roundtrip(tmp_path):
    original = objects([Polygon([(0,0),(10,0),(10,15),(0,15)], [[(2,2),(4,2),(4,4),(2,4)]]),
                        box(10,0,20,15), box(20,0,30,15), box(40,0,50,15), box(50,0,60,15), box(80,0,90,15)])
    before = [(f['geometry'].wkb, f['properties'].copy()) for f in original.features]
    updated = edit_geometry(original, [3,2,1,5,4,6], 'merge')
    assert_coverage(original, updated)
    assert [f['properties']['object_id'] for f in updated.features] == [3,5,6]
    assert [f['properties']['unit_name'] for f in updated.features] == ['Свита 3','Свита 5','Свита 6']
    assert len(updated.features[0]['geometry'].interiors) == 1
    assert updated.features[2]['properties'] == original.features[5]['properties']
    assert updated.features[2]['geometry'].equals_exact(original.features[5]['geometry'],0)
    assert before == [(f['geometry'].wkb, f['properties']) for f in original.features]
    target = tmp_path/'Несколько групп.geotrace'
    save_project(target, updated.raster, updated)
    loaded = load_project(target).result
    for actual, expected in zip(loaded.features,updated.features):
        assert actual['geometry'].equals_exact(expected['geometry'],0)
        assert actual['properties'] == expected['properties']


def test_lasso_intersection_not_centroid_and_no_boundary_only_or_hole():
    result = objects([box(0,0,10,15), box(10,0,20,15),
                      Polygon([(40,0),(60,0),(60,15),(40,15)], [[(45,3),(55,3),(55,12),(45,12)]])])
    tree = STRtree([f['geometry'] for f in result.features])
    # A sliver of polygon 1 counts, while mere contact at polygon 2 does not.
    assert lasso_indices(result,tree,[(9,1),(10,1),(10,2),(9,2)]) == [0]
    assert lasso_indices(result,tree,[(46,4),(54,4),(54,11),(46,11)]) == []
    assert lasso_indices(result,tree,[(8,1),(12,1),(12,2),(8,2)]) == [0,1]
    assert lasso_indices(result,tree,[(0,0),(1,0),(2,0)]) == []
    assert lasso_indices(result,tree,[(1,1),(19,14),(1,14),(19,1)]) == [0,1]
    for bad in ([], [[0,0],[1,0],[np.nan,1]], [[0,0,1]]*3):
        with pytest.raises(ValueError):
            lasso_indices(result,tree,bad)


def test_backend_unlimited_toggle_additive_lasso_direct_merge_and_single_undo(tmp_path):
    application = QGuiApplication.instance() or QGuiApplication([])
    backend = Backend(settings=QSettings(str(tmp_path/'settings.ini'), QSettings.IniFormat), recovery_dir=tmp_path/'recovery')
    backend.autosave_timer.stop()
    original=objects([box(0,0,10,15), box(10,0,20,15), box(20,0,30,15), box(40,0,50,15)])
    backend.raster=original.raster
    backend.notify(width=100,height=60)
    backend.set_result(original)
    backend.setEditTool(0)
    try:
        for x in (5,15,25,45): backend.selectForEdit(x,5)
        assert len(backend.edit_selected)==4
        backend.selectForEdit(45,5)
        assert len(backend.edit_selected)==3
        backend.selectLasso(json.dumps([[40,1],[49,1],[49,14],[40,14]]),True)
        assert len(backend.edit_selected)==4
        backend.selectLasso(json.dumps([[1,1],[29,1],[29,14],[1,14]]),False)
        assert len(backend.edit_selected)==3
        backend.mergeSelected()
        backend.future.result(timeout=20)
        backend.poll()
        assert not backend.data['error'],backend.data['status']
        assert len(backend.result.features)==2 and not backend.data['geometryReady']
        assert len(backend.undo_stack)==1
        backend.undo()
        assert backend.result is original
        backend.selectLasso(json.dumps([[0,0],[1,0],[2,0]]),False)
        assert not backend.edit_selected and not backend.data['editSelected']
    finally:
        backend.timer.stop();backend.web_map.close();backend.basemap_loader.close()
        backend.pool.shutdown(wait=True,cancel_futures=True);backend.temp.cleanup()
