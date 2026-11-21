"""Stamp a release artifact with its authoritative Checks workflow run number."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--number', type=int, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--run-url', default='')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.number <= 0:
        parser.error('number must be positive')
    if len(args.commit) != 40 or any(c not in '0123456789abcdef' for c in args.commit):
        parser.error('commit must be a full lowercase SHA-1')
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    info = dict(version=version, number=args.number, commit=args.commit,
                run_url=args.run_url, built_utc=datetime.now(timezone.utc).isoformat())
    path = args.output or root / 'src/geotrace/build_info.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'GeoTrace {version}, build {args.number}, commit {args.commit}')


if __name__ == '__main__':
    main()
