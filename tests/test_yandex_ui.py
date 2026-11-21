import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from concurrent.futures import Future
from pathlib import Path
import uuid
import zipfile

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from geotrace import app as module, basemap
from geotrace.app import Backend
from geotrace.core import save_preview, segment
from geotrace.project import load_project
from test_core import sample


def test_switch_satellite_yandex_off_key_storage_and_project_portability(tmp_path, monkeypatch):
    QQuickStyle.setStyle('Basic')
    application = QGuiApplication.instance() or QGuiApplication([])
    calls = []
    def render(spec, cache, folder, cancel):
        calls.append(spec.copy())
        path = Path(folder)/('test-'+uuid.uuid4().hex+'.png')
        Image.new('RGBA', (spec['width'], spec['height']), (190, 205, 190, 255)).save(path)
        return dict(path=str(path), rect=spec['rect'], credit=basemap.YANDEX_CREDIT if spec['source']=='yandex' else basemap.EOX_CREDIT,
                    note='Яндекс · схема' if spec['source']=='yandex' else 'EOX · снимки 2016 года')
    monkeypatch.setattr(basemap, 'render_basemap', render)
    settings = QSettings(str(tmp_path/'settings.ini'), QSettings.IniFormat)
    settings.setValue('basemap/yandexTilesApiKey', '')
    backend = Backend(recovery_dir=tmp_path/'recovery', settings=settings)
    backend.autosave_timer.stop()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent/'qml/Main.qml')))
    window = engine.rootObjects()[0]

    def visual(name, item=None):
        item = item or window.contentItem()
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = visual(name, child)
            if found is not None:
                return found

    def reveal(name):
        QTest.qWait(60)  # Wait for the first toolbar layout before clicking its new layer button.
        if not window.property('layersMode'):
            click('layersButton')
        item = visual(name)
        scroll = window.findChild(QObject, 'sidebarScroll').property('contentItem')
        distance = item.mapToScene(QPointF(0, 0)).y()-scroll.mapToScene(QPointF(0, 0)).y()
        target = scroll.property('contentY')+distance-60
        scroll.setProperty('contentY', max(0, min(target, scroll.property('contentHeight')-scroll.property('height'))))
        QTest.qWait(100)

    def click(name):
        item = visual(name)
        assert item and item.isEnabled() and item.isVisible(), name
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                         item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())
        QTest.qWait(150)

    def wait_map(source):
        for _ in range(200):
            QTest.qWait(50)
            if (backend.data['basemapUrl'] and not backend.data['basemapLoading']
                    and backend.view['basemap_source']==source):
                QTest.qWait(100)
                return
        assert False, backend.data

    def wait_job():
        for _ in range(200):
            QTest.qWait(50)
            if not backend.data['busy']:
                break
        assert not backend.data['busy'] and not backend.data['error'], backend.data['status']

    try:
        raster = sample()
        save_preview(raster, Path(backend.temp.name)/'raster.png')
        backend.kind = 'load'
        backend.future = Future()
        backend.future.set_result(raster)
        backend.poll()
        preview = segment(raster, 2, 10)
        backend.auto_preview = preview
        backend.auto_revision = backend.edit_revision
        revision = backend.edit_revision
        backend.notify(autoReady=True)
        reveal('basemapVisible')
        click('basemapVisible')
        wait_map('eox2016')
        reveal('basemapSource')
        click('basemapSource')
        QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Return)
        QTest.qWait(300)
        assert backend.view['basemap_source'] == 'yandex'
        assert window.findChild(QObject, 'yandexKeyDialog').property('visible')
        window.findChild(QObject, 'yandexKey').setProperty('text', 'test-private-key')
        click('saveYandexKey')
        wait_map('yandex')
        assert backend.yandex_key == 'test-private-key'
        assert settings.value('basemap/yandexTilesApiKey') == 'test-private-key'
        assert calls[-1]['api_key'] == 'test-private-key'
        assert visual('yandexLogo').isVisible()
        assert backend.auto_preview is preview and backend.edit_revision == revision
        assert 'test-private-key' not in str(backend.data)
        saved = tmp_path/'project.geotrace'
        backend.saveProject(QUrl.fromLocalFile(str(saved)))
        wait_job()
        restored = load_project(saved)
        assert restored.view['basemap_source'] == 'yandex' and restored.view['basemap_visible']
        with zipfile.ZipFile(saved) as archive:
            assert b'test-private-key' not in archive.read('project.json')
        reveal('basemapVisible')
        click('basemapVisible')
        assert not backend.view['basemap_visible'] and not backend.data['basemapUrl']
        assert not visual('yandexLogo').isVisible()
        rendered = len(calls)
        click('basemapVisible')
        wait_map('yandex')
        assert len(calls) == rendered, 'Enabling an unchanged view must reuse the rendered image'
        click('basemapVisible')
        reveal('basemapSource')
        click('basemapSource')
        QTest.keyClick(window, Qt.Key_Up)
        QTest.keyClick(window, Qt.Key_Return)
        QTest.qWait(150)
        assert backend.view['basemap_source'] == 'eox2016'
        reveal('basemapVisible')
        click('basemapVisible')
        wait_map('eox2016')
        assert not visual('yandexLogo').isVisible()
        assert backend.auto_preview is preview
        assert not backend.setYandexKey('bad key')
        assert backend.yandex_key == 'test-private-key'
        assert backend.setYandexKey('')
        assert not backend.data['yandexKeyConfigured']
        assert backend.resetYandexKey()
        from geotrace.basemap_config import DEFAULT_YANDEX_TILES_API_KEY
        uses_default = backend.yandex_key == DEFAULT_YANDEX_TILES_API_KEY
        assert uses_default and backend.data['yandexKeyConfigured']
        assert not settings.contains('basemap/yandexTilesApiKey')
        assert not warnings, warnings
    finally:
        backend.timer.stop()
        backend.basemap_loader.close()
        window.setProperty('closingConfirmed', True)
        window.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        del engine
        backend.temp.cleanup()

def test_standard_key_is_available_on_first_launch_and_personal_key_takes_precedence(tmp_path):
    application = QGuiApplication.instance() or QGuiApplication([])
    settings = QSettings(str(tmp_path/'fresh.ini'), QSettings.IniFormat)
    backend = Backend(recovery_dir=tmp_path/'recovery', settings=settings)
    backend.autosave_timer.stop()
    try:
        from geotrace.basemap_config import DEFAULT_YANDEX_TILES_API_KEY
        uses_default = backend.yandex_key == DEFAULT_YANDEX_TILES_API_KEY
        assert uses_default and backend.data['yandexKeyConfigured']
        assert backend.setYandexKey('personal-test-key')
        assert backend.yandex_key == 'personal-test-key'
        assert backend.resetYandexKey()
        uses_default = backend.yandex_key == DEFAULT_YANDEX_TILES_API_KEY
        assert uses_default
    finally:
        backend.timer.stop()
        backend.basemap_loader.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        backend.temp.cleanup()
