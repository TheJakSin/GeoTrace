from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root=Path(SPECPATH).parent
assets=root/'build/branding'
data=collect_data_files('geotrace')
for package in ('rasterio','fiona'):
    data.extend(collect_data_files(package))
data.extend([(str(root/'build/manual/USER_GUIDE.html'),'geotrace/resources/docs'),
             (str(root/'build/manual/images'),'geotrace/resources/docs/images'),
             (str(root/'build/notices'),'geotrace/resources/notices')])
hidden=collect_submodules('geotrace')+['PySide6.QtWebEngineQuick','PySide6.QtQuickControls2','PySide6.QtSvg','PySide6.QtNetwork']
hidden+=collect_submodules('rasterio')+collect_submodules('fiona')
a=Analysis([str(root/'packaging/entry.py')],pathex=[str(root/'src')],
    binaries=[],datas=data,hiddenimports=hidden,hookspath=[],runtime_hooks=[str(root/'packaging/runtime.py')],
    excludes=['torch','torchvision','timm','tensorflow','matplotlib','IPython','pytest','tkinter','PyQt5','PyQt6','PySide2'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='GeoTrace',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False,
    icon=str(assets/('GeoTrace.icns' if sys.platform=='darwin' else 'GeoTrace.ico')),
    version=str(assets/'version.txt') if sys.platform=='win32' else None)
bundle=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='GeoTrace')
if sys.platform=='darwin':
    app=BUNDLE(bundle,name='GeoTrace.app',icon=str(assets/'GeoTrace.icns'),
        bundle_identifier='com.tjs.geotrace',info_plist={
            'CFBundleShortVersionString':__import__('geotrace').__version__,
            'CFBundleVersion':str(__import__('geotrace.release_info',fromlist=['read_build_info']).read_build_info().get('number',1)),
            'NSHighResolutionCapable':True,'LSMinimumSystemVersion':'14.4',
            'CFBundleDocumentTypes':[{'CFBundleTypeName':'GeoTrace project','CFBundleTypeExtensions':['geotrace'],
                'CFBundleTypeRole':'Editor','LSHandlerRank':'Alternate','LSItemContentTypes':['com.tjs.geotrace.project']}],
            'UTExportedTypeDeclarations':[{'UTTypeIdentifier':'com.tjs.geotrace.project',
                'UTTypeDescription':'GeoTrace project','UTTypeConformsTo':['public.data'],
                'UTTypeTagSpecification':{'public.filename-extension':['geotrace']}}]})
