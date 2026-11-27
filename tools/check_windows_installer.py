"""Exercise installer transactions before freezing the full application."""
from pathlib import Path
import os
import subprocess
import tempfile
import winreg
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]

def main():
    compiler=Path(os.environ['ProgramFiles(x86)'])/'NSIS/makensis.exe'
    with tempfile.TemporaryDirectory(prefix='geotrace-installer-') as temporary:
        work=Path(temporary);assets=work/'branding';assets.mkdir();payload=work/'payload';payload.mkdir()
        (payload/'_internal').mkdir();(payload/'GeoTrace.exe').write_bytes(b'installer fixture')
        (payload/'_internal/payload.txt').write_text('new payload')
        Image.new('RGBA',(64,64),'#263445').save(assets/'GeoTrace.ico')
        Image.new('RGB',(164,314),'#263445').save(assets/'wizard.bmp')
        Image.new('RGB',(150,57),'white').save(assets/'header.bmp')
        setup=work/'Setup.exe';destination=work/'Installed GeoTrace'
        subprocess.run([str(compiler),'/WX','/INPUTCHARSET','UTF8','/DVERSION=1.1.0','/DBUILD=1','/DPAYLOAD_KB=1',f'/DOUTPUT={setup}',f'/DAPPDIR={payload}',f'/DASSETS={assets}',str(ROOT/'packaging/windows.nsi')],check=True)
        log=Path(os.environ['TEMP'])/'GeoTrace-install.log';log.unlink(missing_ok=True)
        for attempt in range(2):
            result=subprocess.run([str(setup),'/S',f'/D={destination}'],timeout=120)
            print('Installer attempt',attempt+1,'exit',result.returncode,flush=True)
            if result.returncode:
                print(log.read_text(errors='replace') if log.exists() else 'No failure log',flush=True)
                raise SystemExit(result.returncode)
            assert (destination/'GeoTrace.exe').read_bytes()==b'installer fixture'
            assert (destination/'_internal/payload.txt').read_text()=='new payload'
            if attempt==0:(destination/'_internal/obsolete.txt').write_text('remove during upgrade')
        assert not (destination/'_internal/obsolete.txt').exists()
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\GeoTrace',0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            assert winreg.QueryValueEx(key,'UninstallString')[0]=='"'+str(destination/'Uninstall.exe')+'"'
        print('Fresh installation, update, obsolete-file removal and quoted uninstall path passed.',flush=True)
        subprocess.run([str(destination/'Uninstall.exe'),'/S',f'_?={destination}'],check=True,timeout=120)

if __name__=='__main__':main()
