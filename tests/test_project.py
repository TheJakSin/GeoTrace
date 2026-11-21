"""Persistence checks cover user data and interrupted writes, not UI cosmetics."""
import json
import zipfile

import numpy as np
import pytest

from geotrace import project
from geotrace.core import segment
from geotrace.project import load_project, save_project
from geotrace.attributes import change_schema, SUGGESTED_FIELDS
from test_core import sample


def test_portable_project_preserves_geometry_attributes_grid_and_hints(tmp_path):
    raster = sample()  # Source path deliberately does not exist.
    result = change_schema(segment(raster, 2, 10), SUGGESTED_FIELDS[:2])
    result.features[0]['properties'].update(unit_index='O₂cr', unit_name='Чертовская свита', review='edited')
    path = tmp_path / 'Геология.geotrace'
    save_project(path, raster, result, [(10,10,1), (80,10,0)], (0,0,100,60))
    restored = load_project(path)
    assert restored.raster.transform == raster.transform
    assert restored.raster.crs_wkt == raster.crs_wkt
    assert restored.raster.source_size == raster.source_size
    np.testing.assert_array_equal(restored.raster.rgb, raster.rgb)
    np.testing.assert_array_equal(restored.raster.valid, raster.valid)
    np.testing.assert_array_equal(restored.result.labels, result.labels)
    assert restored.result.colors == result.colors
    for actual, expected in zip(restored.result.features, result.features, strict=True):
        assert actual['geometry'].equals_exact(expected['geometry'], 0)
        assert actual['properties'] == expected['properties']
    assert restored.points == [(10,10,1), (80,10,0)]
    assert restored.roi == (0,0,100,60)


def test_failed_replacement_retains_last_saved_project(tmp_path, monkeypatch):
    path = tmp_path / 'project.geotrace'
    save_project(path, sample())
    before = path.read_bytes()
    def interrupted(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(project.os, 'replace', interrupted)
    with pytest.raises(OSError, match='disk unavailable'):
        save_project(path, sample(), segment(sample(), 2, 10))
    assert path.read_bytes() == before
    assert load_project(path).result is None
    assert list(tmp_path.iterdir()) == [path]


def test_future_or_corrupted_project_is_rejected(tmp_path):
    path = tmp_path / 'broken.geotrace'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('project.json', json.dumps(dict(format=project.FORMAT, version=999)))
    with pytest.raises(ValueError, match='версия'):
        load_project(path)
    path.write_bytes(b'incomplete write')
    with pytest.raises(ValueError, match='открыть проект'):
        load_project(path)


def test_legacy_filled_geology_becomes_optional_and_empty_field_is_removed(tmp_path):
    raster = sample()
    old = segment(raster,2,10)
    for f in old.features:
        f['properties'].update(unit_name='',unit_index='')
    old.features[0]['properties']['unit_index']='J₂'
    path = tmp_path/'sample.geotrace'
    save_project(path,raster,old)
    with zipfile.ZipFile(path) as archive:
        files = {key:archive.read(key) for key in archive.namelist()}
    meta = json.loads(files['project.json'])
    meta['version']=2
    files['project.json']=json.dumps(meta).encode()
    with zipfile.ZipFile(path,'w') as archive:
        for key,data in files.items():
            archive.writestr(key,data)
    restored = load_project(path).result
    assert [f['key'] for f in restored.fields]==['unit_index']
    assert restored.features[0]['properties']['unit_index']=='J₂'
    assert all('unit_name' not in f['properties'] for f in restored.features)
