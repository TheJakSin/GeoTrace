"""Run the actual binary with an isolated profile and no Python environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);parser.add_argument('--report',required=True,type=Path);args=parser.parse_args()
    environment={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','VIRTUAL_ENV','QT_PLUGIN','QML2_IMPORT','QML_IMPORT'))}
    environment['QT_QUICK_BACKEND']='software'
    environment['QT_QPA_PLATFORM']='offscreen'
    environment['QTWEBENGINE_CHROMIUM_FLAGS']='--disable-gpu'
    with tempfile.TemporaryDirectory(prefix='geotrace-frozen-check-') as work:
        subprocess.run([str(args.executable.resolve()),'--self-check',str(args.report.resolve())],cwd=work,env=environment,check=True,timeout=240)
    result=json.loads(args.report.read_text(encoding='utf-8'))
    if not result['ok']:raise SystemExit(result['error'])
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
