"""Copy only the reviewed documents whose source text and output hashes match."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tomllib

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version'];series='.'.join(version.split('.')[:2])
    folder=ROOT/'docs'/('release-'+series)
    if not folder.is_dir():folder=ROOT/'docs/release'
    sources=json.loads((folder/'sources.json').read_text())
    for name,digest in sources.items():
        assert hashlib.sha256((ROOT/'docs'/name).read_bytes()).hexdigest()==digest, 'Review and regenerate documents after editing '+name
    expected={f'GeoTrace-{series}-{label}.{ext}' for label in ('Руководство','Возможности') for ext in ('docx','pdf')}
    checked={}
    for line in (folder/'SHA256SUMS.txt').read_text().splitlines():
        digest,name=line.split('  ',1)
        assert name in expected
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest,name
        checked[name]=digest
    assert set(checked)==expected
    args.output.mkdir(parents=True,exist_ok=True)
    for name in expected:shutil.copyfile(folder/name,args.output/name)
    print(f'Checked {version}: reviewed source texts and {len(expected)} document hashes')


if __name__=='__main__':main()
