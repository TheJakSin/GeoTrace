import json
import subprocess
import sys
from pathlib import Path

import pytest
from geotrace import __version__
from geotrace.release_info import read_build_info


def test_build_stamp_roundtrip(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'build.json'
    commit = 'a' * 40
    subprocess.run([sys.executable, str(root / 'tools/write_build_info.py'),
                    '--number', '7', '--commit', commit, '--output', str(output)], check=True)
    info = read_build_info(output)
    assert (info['version'], info['number'], info['commit']) == (__version__, 7, commit)
    assert info['built_utc']


@pytest.mark.parametrize('payload', [None, [], {'version': '0.3.0a1', 'number': 7},
    {'version': __version__, 'number': True}, {'version': __version__, 'number': -1},
    {'version': __version__, 'number': 7, 'commit': 'bad'}])
def test_stale_or_malformed_metadata_is_dev(tmp_path, payload):
    output = tmp_path / 'build.json'
    output.write_text(json.dumps(payload), encoding='utf-8')
    assert read_build_info(output) == {}


def test_missing_or_broken_metadata_is_dev(tmp_path):
    path = tmp_path / 'missing.json'
    assert read_build_info(path) == {}
    path.write_text('{', encoding='utf-8')
    assert read_build_info(path) == {}
