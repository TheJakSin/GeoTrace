"""Run the complete suite with one native Qt application process per UI scenario.

Qt Quick type registrations survive engine destruction. Keeping unrelated UI test
scenarios in separate processes also reflects the one-editor-engine app lifecycle.
No tests are filtered out; every collected module is included once.
"""
from pathlib import Path
import subprocess,sys,tempfile,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]

def main():
 ui=[p for p in sorted((ROOT/'tests').glob('test_*.py')) if 'QQmlApplicationEngine' in p.read_text(encoding="utf-8") or 'from test_workspace_ux import' in p.read_text(encoding="utf-8")]
 count=failed=0
 collection=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q',*[str(p.relative_to(ROOT)) for p in ui]],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
 if collection.returncode:
  print(collection.stdout,collection.stderr);return collection.returncode
 nodes=[line.strip() for line in collection.stdout.splitlines() if '::' in line and line.strip().startswith(('tests/','tests\\'))]
 if not nodes:raise RuntimeError('No UI scenarios collected')
 with tempfile.TemporaryDirectory(prefix='geotrace-tests-') as folder:
  groups=[['tests',*['--ignore='+str(p.relative_to(ROOT)) for p in ui]]]+[[node] for node in nodes]
  for i,group in enumerate(groups):
   report=Path(folder)/f'{i}.xml'
   result=subprocess.run([sys.executable,'-m','pytest','-q',*group,'--junitxml='+str(report)],cwd=ROOT)
   if report.exists():
    for suite in ET.parse(report).getroot().iter('testsuite'):
     count+=int(suite.get('tests',0));failed+=int(suite.get('failures',0))+int(suite.get('errors',0))
   if result.returncode:
    print(f'Suite failed in {group[0]}',flush=True);return result.returncode
 print(f'Complete suite: {count} tests, {failed} failures; {len(nodes)} UI scenarios isolated by process.',flush=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
