import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


@pytest.mark.parametrize('aligned', [True, False])
def test_quality_benchmark_cli_writes_scored_json_or_rejects_wrong_grid(tmp_path, aligned):
    rgb = np.empty((3, 80, 120), np.uint8)
    rgb[:, :, :60] = np.array([235, 180, 120])[:, None, None]
    rgb[:, :, 60:] = np.array([120, 210, 235])[:, None, None]
    source = tmp_path / 'map.tif'
    with rasterio.open(source, 'w', driver='GTiff', width=120, height=80, count=3,
                       dtype='uint8', crs='EPSG:32637', transform=from_origin(400000, 6000000, 10, 10)) as dataset:
        dataset.write(rgb)
    reference = np.ones((80, 120) if aligned else (10, 10), np.int32)
    if aligned:
        reference[:, 60:] = 2
    np.save(tmp_path / 'reference.npy', reference)
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'report.json'
    process = subprocess.run([sys.executable, str(root / 'tools/benchmark_quality.py'),
                              '--raster', str(source), '--reference', str(tmp_path / 'reference.npy'),
                              '--output', str(output)], capture_output=True, text=True,
                             env={**os.environ, 'PYTHONIOENCODING': 'cp1252:strict'})
    if aligned:
        assert process.returncode == 0, process.stderr
        report = json.loads(output.read_text(encoding='utf-8'))
        assert report['reference_objects'] == 2
        assert 0.9 < report['mean_iou'] <= 1
        assert all(report[k] for k in ('valid_geometry', 'complete_coverage', 'no_overlaps'))
        assert json.loads(process.stdout)['reference_objects'] == 2
    else:
        assert process.returncode != 0
        assert 'working raster grid' in process.stderr
        assert not output.exists()
