"""Build native applications and installers only after the feature/test stage."""
import math
import argparse
import hashlib
from html import escape
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def run(*args,**kwargs):
    subprocess.run([str(a) for a in args],check=True,**kwargs)


def branding(folder,version,number):
    from PySide6.QtCore import QByteArray,Qt
    from PySide6.QtGui import QGuiApplication,QImage,QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image,ImageDraw,ImageFont
    application=QGuiApplication.instance() or QGuiApplication([])
    folder.mkdir(parents=True,exist_ok=True)
    renderer=QSvgRenderer(str(ROOT/'src/geotrace/qml/icons/geotrace.svg'))
    image=QImage(1024,1024,QImage.Format_ARGB32);image.fill(Qt.transparent)
    painter=QPainter(image);renderer.render(painter);painter.end();image.save(str(folder/'GeoTrace.png'))
    icon=Image.open(folder/'GeoTrace.png')
    icon.save(folder/'GeoTrace.ico',sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
    if sys.platform=='darwin':icon.save(folder/'GeoTrace.icns')
    candidates=[Path('/System/Library/Fonts/Supplemental/Arial.ttf'),Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/segoeui.ttf',Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font_path=next((p for p in candidates if p.is_file()),None)
    def font(size):return ImageFont.truetype(str(font_path),size) if font_path else ImageFont.load_default(size=size)
    wizard=Image.new('RGB',(328,628),'#172b49');draw=ImageDraw.Draw(wizard)
    for i in range(7):
        draw.arc((-160+i*20,180+i*23,580-i*23,920-i*28),170,350,fill='#254668',width=2)
    wizard.paste(icon.resize((116,116)),(36,42),icon.resize((116,116)))
    draw.text((36,177),'GeoTrace',font=font(35),fill='white')
    draw.text((36,228),version,font=font(20),fill='#aec4df')
    draw.text((36,530),'TJS TECH',font=font(18),fill='#dbe7f5')
    draw.text((36,558),'× TJS HORIZON',font=font(18),fill='#dbe7f5')
    wizard.resize((164,314)).save(folder/'wizard.bmp')
    header=Image.new('RGB',(300,114),'white');logo=icon.resize((80,80));header.paste(logo,(14,17),logo)
    ImageDraw.Draw(header).text((104,42),'GeoTrace',font=font(28),fill='#263445');header.resize((150,57)).save(folder/'header.bmp')
    numeric=','.join(version.split('.')[:3]+[str(number)])
    (folder/'version.txt').write_text("VSVersionInfo(ffi=FixedFileInfo(filevers=("+numeric+"),prodvers=("+numeric+"),mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('CompanyName','TJS PRIME · TJS MERIDIAN'),StringStruct('FileDescription','GeoTrace'),StringStruct('FileVersion','"+version+" build "+str(number)+"'),StringStruct('ProductName','GeoTrace'),StringStruct('ProductVersion','"+version+"')])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])",encoding='utf-8')


def manual(folder):
    import markdown
    folder.mkdir(parents=True,exist_ok=True)
    source=(ROOT/'docs/USER_GUIDE.md').read_text(encoding='utf-8')
    html=markdown.markdown(source,extensions=['tables','fenced_code','toc'])
    style='body{max-width:950px;margin:40px auto;padding:0 24px;font:16px/1.65 Arial,sans-serif;color:#303944}h1,h2,h3{color:#263445;line-height:1.3}a{color:#356ac3}table{border-collapse:collapse;width:100%}th,td{padding:8px;border:1px solid #dce0e6;text-align:left}pre{background:#f1f3f6;padding:16px;overflow:auto}img{max-width:100%}'
    (folder/'USER_GUIDE.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>GeoTrace — руководство</title><style>'+style+'</style><body>'+html+'</body></html>',encoding='utf-8')
    images=ROOT/'docs/images'
    if images.is_dir():shutil.copytree(images,folder/'images',dirs_exist_ok=True)


def notices(folder):
    folder.mkdir(parents=True,exist_ok=True)
    packages=['PySide6','PySide6-Essentials','PySide6-Addons','PySide6-WebEngine','PySide6-Pdf','shiboken6','rasterio','fiona','pyproj','shapely','numpy','scipy','scikit-image','pillow','opencv-python-headless','certifi','pyinstaller']
    lines=['GeoTrace использует открытые библиотеки. Уведомления и лицензии поставляются рядом с приложением.\n']
    for name in packages:
        try:
            distribution=importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        lines.append(name+' '+distribution.version+'\n')
        for item in distribution.files or []:
            if any(token in str(item).upper() for token in ('LICENSE','LICENCE','COPYING','NOTICE')):
                source=Path(distribution.locate_file(item))
                if source.is_file():
                    target=folder/name/str(item).replace('..','_')
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    (folder/'COMPONENTS.txt').write_text(''.join(lines),encoding='utf-8')
    if (ROOT/'LICENSE').is_file():shutil.copyfile(ROOT/'LICENSE',folder/'GeoTrace-LICENSE.txt')


def mac_installer(app,output,assets,version,number):
    work=ROOT/'build/mac-package';work.mkdir(parents=True,exist_ok=True)
    payload=work/'root'
    if payload.exists():shutil.rmtree(payload)
    payload.mkdir()
    staged=payload/app.name
    shutil.copytree(app,staged,symlinks=True)
    sign_mac(staged)
    resources=work/'resources';resources.mkdir(exist_ok=True)
    shutil.copyfile(assets/'GeoTrace.png',resources/'logo.png')
    welcome=f'''<html><meta charset="utf-8"><body style="font-family:Helvetica;color:#303944"><img src="logo.png" width="72" height="72"><h1>GeoTrace {escape(version)}</h1><p>Оцифровка карт и работа с участками</p><p>Полигоны и линии, несколько листов, атрибуты и выпуск карт.</p><p>Сборка {number}</p><br><p style="color:#687383">TJS TECH × TJS HORIZON<br>TJS PRIME · TJS MERIDIAN / DATA<br>Предметные задачи — TJS GEOPHYSICS<br>Исследования — TJS XLAB</p></body></html>'''
    (resources/'Welcome.html').write_text(welcome,encoding='utf-8')
    (resources/'Conclusion.html').write_text('<html><meta charset="utf-8"><body style="font-family:Helvetica;color:#303944"><h1>GeoTrace установлен</h1><p>Откройте GeoTrace в папке «Программы». Руководство доступно в меню «О программе».</p><p>Ваши карты и проекты сохраняются отдельно от приложения.</p></body></html>',encoding='utf-8')
    component=work/'component.pkg'
    plist=work/'component.plist'
    run('pkgbuild','--analyze','--root',payload,plist)
    import plistlib
    values=plistlib.loads(plist.read_bytes())
    values=[v for v in values if v.get('RootRelativeBundlePath')==app.name]
    for value in values:value.update(BundleIsRelocatable=False,BundleHasStrictIdentifier=True,BundleIsVersionChecked=True,BundleOverwriteAction='upgrade')
    plist.write_bytes(plistlib.dumps(values))
    run('pkgbuild','--root',payload,'--component-plist',plist,'--identifier','com.tjs.geotrace','--version',version,'--install-location','/Applications',component)
    distribution=work/'Distribution.xml'
    distribution.write_text(f'''<?xml version="1.0"?><installer-gui-script minSpecVersion="2"><title>GeoTrace {escape(version)}</title><welcome file="Welcome.html"/><conclusion file="Conclusion.html"/><options customize="never" require-scripts="false"/><domains enable_localSystem="true" enable_currentUserHome="false" enable_anywhere="false"/><choices-outline><line choice="geotrace"/></choices-outline><choice id="geotrace" title="GeoTrace" visible="false"><pkg-ref id="com.tjs.geotrace"/></choice><pkg-ref id="com.tjs.geotrace" version="{version}" onConclusion="none">component.pkg</pkg-ref></installer-gui-script>''',encoding='utf-8')
    run('productbuild','--distribution',distribution,'--resources',resources,'--package-path',work,output)


def sign_mac(app):
    # File Provider folders can attach Finder metadata to a new bundle. Remove
    # only these build artifacts; never clear quarantine or user security data.
    for name in ('com.apple.FinderInfo','com.apple.ResourceFork'):
        # macOS Python does not provide Linux's os.listxattr bindings. xattr may
        # return nonzero when a file has no such attribute; codesign verifies it.
        subprocess.run(['xattr','-r','-d',name,str(app)],capture_output=True,check=False)
    run('codesign','--force','--deep','--sign','-',app)
    run('codesign','--verify','--deep','--strict',app)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'dist/desktop');parser.add_argument('--skip-freeze',action='store_true');args=parser.parse_args()
    from geotrace import __version__
    from geotrace.release_info import read_build_info
    info=read_build_info()
    if not info:
        raise SystemExit('Stamp an actual Checks build identity before creating distribution installers.')
    # Qt 6.12 distributes WebEngine separately from Addons. Require the native
    # module before freezing rather than silently producing an incomplete tab.
    import importlib.util
    if importlib.util.find_spec('PySide6.QtWebEngineQuick') is None:
        raise SystemExit('Install the full PySide6 dependency, including WebEngine, before packaging.')
    version=__version__;number=info['number'];arch=platform.machine().lower().replace('amd64','x64')
    if sys.platform not in ('win32','darwin'):raise SystemExit('Native Windows/macOS build required.')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    assets=ROOT/'build/branding';branding(assets,version,number);manual(ROOT/'build/manual');notices(ROOT/'build/notices')
    frozen=ROOT/'build/frozen'
    if not args.skip_freeze:
        run(sys.executable,'-m','PyInstaller','--noconfirm','--distpath',frozen,'--workpath',ROOT/'build/pyinstaller',ROOT/'packaging/GeoTrace.spec',cwd=ROOT)
    app=frozen/('GeoTrace.app' if sys.platform=='darwin' else 'GeoTrace')
    if sys.platform=='darwin':sign_mac(app)
    executable=app/'Contents/MacOS/GeoTrace' if sys.platform=='darwin' else app/'GeoTrace.exe'
    run(sys.executable,ROOT/'tools/smoke_frozen.py',executable,'--report',out/f'selfcheck-{sys.platform}-{arch}.json')
    stem=f'GeoTrace-{version}-build{number}-'+('macOS' if sys.platform=='darwin' else 'Windows')+'-'+arch
    if sys.platform=='darwin':
        mac_installer(app,out/(stem+'.pkg'),assets,version,number)
        run('ditto','-c','-k','--sequesterRsrc','--keepParent',app,out/(stem+'.zip'))
    else:
        nsis=shutil.which('makensis') or str(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'NSIS/makensis.exe')
        payload_kb=math.ceil(sum(p.stat().st_size for p in app.rglob('*') if p.is_file())/1024)
        run(nsis,'/INPUTCHARSET','UTF8',f'/DPAYLOAD_KB={payload_kb}',f'/DVERSION={version}',f'/DBUILD={number}',f'/DOUTPUT={out/(stem+"-Setup.exe")}',f'/DAPPDIR={app}',f'/DASSETS={assets}',ROOT/'packaging/windows.nsi')
        shutil.make_archive(str(out/stem),'zip',root_dir=app.parent,base_dir=app.name)
    checksums=[]
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name!='SHA256SUMS.txt':
            with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            checksums.append(digest+'  '+path.name)
    (out/'SHA256SUMS.txt').write_text('\n'.join(checksums)+'\n',encoding='utf-8')


if __name__=='__main__':main()
