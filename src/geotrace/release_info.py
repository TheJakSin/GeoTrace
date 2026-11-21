"""Release identity; a source checkout without CI metadata remains a dev build."""
import json
from pathlib import Path

from . import __version__


def read_build_info(path=None):
    try:
        info = json.loads((Path(path) if path else Path(__file__).with_name('build_info.json')).read_text(encoding='utf-8'))
        if not isinstance(info, dict) or info.get('version') != __version__:
            return {}
        number = info.get('number')
        if type(number) is not int or number <= 0:
            return {}
        commit = info.get('commit', '')
        if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            return {}
        return info
    except (OSError, ValueError, TypeError):
        return {}


def version_label():
    info = read_build_info()
    suffix = f"build {info['number']}" if info else 'dev'
    return f'0.8 beta 1 · {suffix}'
