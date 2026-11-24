from dataclasses import replace
import json
import zipfile

import numpy as np
import pytest
from affine import Affine
from pyproj import Geod
from rasterio.crs import CRS
from shapely.geometry import box, LineString
from shapely.ops import unary_union

from geotrace.core import Raster, segment
from geotrace.document import Document, Sheet, save_document, load_document
from geotrace.project import save_project
from geotrace.spatial import measurements, project_geometry


def sheet(name, left, color):
    rgb = np.empty((60, 80, 3), np.uint8)
    rgb[:, :40] = color
    rgb[:, 40:] = [170, 220, 235]
    raster = Raster(name, rgb, np.ones((60, 80), bool), Affine(.001, 0, left, 0, -.001, 60),
                    CRS.from_epsg(4326).to_wkt(), 'EPSG:4326', (80, 60))
    return Sheet(name, name, raster)


def two_sheets():
    a, b = sheet('a', 56, [240, 200, 120]), sheet('b', 56.075, [180, 155, 210])
    doc = Document(sheets=[a, b], active_sheet='a')
    for s in doc.sheets:
        doc.set_sheet_result(s.id, segment(s.raster, 2, 5))
    return doc


def test_geodesic_measurement_is_not_mercator_area_and_accounts_for_holes():
    outer = box(56, 59.9, 56.1, 60)
    hole = box(56.02, 59.92, 56.04, 59.94)
    geometry = outer.difference(hole)
    projected = project_geometry(geometry, 'EPSG:4326')
    result = measurements(projected)
    assert projected.area > 3.8*result['area_m2']
    a = measurements(project_geometry(outer, 'EPSG:4326'))['area_m2']
    b = measurements(project_geometry(hole, 'EPSG:4326'))['area_m2']
    assert abs(result['area_m2']-(a-b)) < 1e-5*a
    line = project_geometry(LineString([(56, 60), (56.1, 60)]), 'EPSG:4326')
    assert line.length > 1.99*measurements(line)['length_m']


def test_common_classes_clip_overlap_but_preserve_full_sheet_data_and_manual_work():
    doc = two_sheets()
    a, b = doc.sheets
    # Different colours on different sheets can represent one semantic class.
    for local in b.groups:
        doc.assign_group(b.id, local, a.groups[local])
    doc.areas = [dict(name='Лицензия', geometry=project_geometry(box(56.025, 59.95, 56.13, 59.99), 'EPSG:4326'))]
    doc.active_area = 0
    canvas = doc.canvas(400)
    result = doc.compose(canvas)
    assert canvas.crs_label == 'EPSG:3857'
    geos = [f['geometry'] for f in result.features]
    union = unary_union(geos)
    assert sum(g.area for g in geos)-union.area < union.area*1e-9
    assert union.symmetric_difference(doc.area).area < union.area*1e-8
    assert all(s.raster.area is None and s.raster.valid.all() for s in doc.sheets)
    protected = result.features[0]
    protected['properties'].update(review='edited', note='Ручная правка')
    doc.manual = result
    # Changing the overview grid must not invalidate world-space manual geometry.
    changed = doc.compose(doc.canvas(300))
    kept = next(f for f in changed.features if f['properties']['object_id'] == protected['properties']['object_id'])
    assert kept['geometry'].equals_exact(protected['geometry'], 0)
    assert kept['properties']['note'] == 'Ручная правка'


def test_workspace_roundtrip_and_legacy_migration(tmp_path):
    doc = two_sheets()
    doc.line_types = [dict(id=1, name='Разлом', colors=['#b53239'], style={})]
    doc.lines = [dict(geometry=project_geometry(LineString([(56.03, 59.97), (56.12, 59.96)]), 'EPSG:4326'),
                      properties=dict(object_id=1, type_id=1, note='', review='edited', origin='manual'))]
    doc.layouts = [dict(name='Лист А3', paper='A3', title='Карта')]
    canvas = doc.canvas(300)
    result = doc.compose(canvas)
    path = tmp_path/'workspace.geotrace'
    save_document(path, doc, canvas, result)
    loaded = load_document(path)
    assert len(loaded.sheets) == 2 and loaded.active_sheet == 'a'
    assert loaded.layouts == doc.layouts
    assert loaded.lines[0]['geometry'].equals_exact(doc.lines[0]['geometry'], 0)
    assert loaded.compose(loaded.canvas(300)).features[0]['geometry'].equals_exact(result.features[0]['geometry'], 0)
    old = tmp_path/'old.geotrace'
    save_project(old, doc.sheets[0].raster, doc.sheets[0].result)
    migrated = load_document(old)
    assert len(migrated.sheets) == 1 and migrated.sheet().result is not None
    assert migrated.canvas(300).crs_label == 'EPSG:3857'


def test_untrusted_embedded_source_cannot_escape_cache(tmp_path):
    doc = two_sheets()
    path = tmp_path/'project.geotrace'
    save_document(path, doc)
    with zipfile.ZipFile(path) as original:
        files = {n: original.read(n) for n in original.namelist()}
    meta = json.loads(files['workspace.json'])
    meta['sheets'][0].update(id='../escape', embedded_source='sources/0.tif')
    files['workspace.json'] = json.dumps(meta).encode()
    files['sources/0.tif'] = b'never extract'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match='идентификатор'):
        load_document(path, tmp_path/'cache')
    assert not (tmp_path/'escape').exists()
