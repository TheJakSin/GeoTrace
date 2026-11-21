"""Offline browser lifecycle check, isolated from the suite's Qt application."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWebEngineQuick import QtWebEngineQuick
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from geotrace import app as module
from geotrace.app import Backend


def main(folder):
    QQuickStyle.setStyle('Basic')
    QtWebEngineQuick.initialize()
    application = QGuiApplication([])
    backend = Backend(settings=QSettings(str(folder/'settings.ini'), QSettings.IniFormat),
                      recovery_dir=folder/'recovery')
    backend.autosave_timer.stop()
    # No external API is contacted. A session counter exposes actual reloads.
    backend.web_map.document = b'''<!doctype html><html><script>
      sessionStorage.loads=String(Number(sessionStorage.loads||0)+1);
      document.title='Loads: '+sessionStorage.loads;
      </script><body>Offline lifecycle check</body></html>'''
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(module.__file__).parent/'qml/Main.qml')))
    window = engine.rootObjects()[0]
    tabs = window.findChild(QObject, 'workspaceTabs')
    phase, updates, browser = 0, 0, None
    failure = []
    timer = QTimer()
    timer.setInterval(50)
    began = time.monotonic()

    def check():
        nonlocal phase, updates, browser
        try:
            assert time.monotonic()-began < 20, 'Browser did not finish loading'
            if phase == 0:
                tabs.setProperty('currentIndex', 1)
                phase = 1
            elif phase == 1:
                browser = window.findChild(QObject, 'webMapBrowser')
                if browser is not None and browser.property('title') == 'Loads: 1':
                    phase = 2
            elif phase == 2:
                if updates == 0:
                    panel=window.findChild(QQuickItem,'webMapLayersPanel')
                    assert not panel.isVisible()
                    width,height=browser.property('width'),browser.property('height')
                    QTest.keyClick(window,Qt.Key_G)
                    assert panel.isVisible(), 'G must open web layers.'
                    QTest.keyClick(window,Qt.Key_G)
                    assert not panel.isVisible()
                    assert (browser.property('width'),browser.property('height'))==(width,height)
                backend.notify(status=f'Unrelated editor update {updates}')
                updates += 1
                assert browser.property('title') == 'Loads: 1'
                if updates == 12:
                    tabs.setProperty('currentIndex', 0)
                    phase = 3
            elif phase == 3:
                tabs.setProperty('currentIndex', 1)
                phase = 4
            elif phase == 4:
                assert window.findChild(QObject, 'webMapBrowser') is browser
                assert browser.property('title') == 'Loads: 1', 'Returning to a tab reloaded the page'
                assert not warnings, warnings
                timer.stop()
                application.quit()
        except Exception as error:
            failure.append(str(error))
            timer.stop()
            application.quit()
    timer.timeout.connect(check)
    timer.start()
    application.exec()
    backend.timer.stop()
    backend.web_map.close()
    backend.basemap_loader.close()
    backend.pool.shutdown(wait=True, cancel_futures=True)
    window.setProperty('closingConfirmed', True)
    window.close()
    del engine
    backend.temp.cleanup()
    assert not failure, failure
    print('Browser loaded once; editor changes and tab switching did not reload it.')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
