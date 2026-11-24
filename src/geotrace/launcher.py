"""Fast first window; import the GIS engine while the product splash is visible."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import multiprocessing
from pathlib import Path
import sys
import time


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument('project', nargs='?')
    parser.add_argument('--map')
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--sam-checkpoint')
    parser.add_argument('--screenshot')
    parser.add_argument('--no-splash', action='store_true')
    parser.add_argument('--startup-log', help='Developer timing log; no map contents or credentials')
    parser.add_argument('--self-check',help='Distribution diagnostics with temporary maps and an isolated profile')
    args = parser.parse_args()
    import tempfile
    diagnostic_profile=tempfile.TemporaryDirectory(prefix='geotrace-diagnostics-') if args.self_check else None
    started = time.perf_counter()
    from PySide6.QtCore import QCoreApplication, QTimer, QUrl, Qt, QObject, QEvent
    from PySide6.QtGui import QGuiApplication, QIcon, QPalette, QColor
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    QQuickStyle.setStyle('Basic')
    # The shared-context attribute above is the documented equivalent of
    # QtWebEngineQuick.initialize(). Import the browser only when its tab opens.
    app = QGuiApplication(sys.argv[:1])
    palette=QPalette()
    for role,color in ((QPalette.Window,'#f3f5f8'),(QPalette.WindowText,'#263445'),
        (QPalette.Base,'#ffffff'),(QPalette.AlternateBase,'#f1f4f8'),(QPalette.Text,'#263445'),
        (QPalette.Button,'#f0f2f5'),(QPalette.ButtonText,'#303944'),(QPalette.PlaceholderText,'#7b8593'),
        (QPalette.Highlight,'#356ac3'),(QPalette.HighlightedText,'#ffffff')):
        palette.setColor(role,QColor(color))
    app.setPalette(palette)
    app.setApplicationName('GeoTrace')
    app.setOrganizationName('GeoTrace')
    app.setWindowIcon(QIcon(str(Path(__file__).parent/'qml/icons/geotrace.svg')))
    from . import __version__
    from .release_info import version_label
    boot_engine = QQmlApplicationEngine()
    if not args.no_splash:
        boot_engine.load(QUrl.fromLocalFile(str(Path(__file__).parent/'qml/Boot.qml')))
        if boot_engine.rootObjects():
            boot_engine.rootObjects()[0].setProperty('version', version_label().split(' · build')[0])
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='geotrace-start')
    state = dict(future=None, backend=None, engine=None, error='', splash_shown=time.perf_counter()-started)
    state['boot_visible_at']=time.perf_counter()
    class FileEvents(QObject):
        def eventFilter(self, obj, event):
            if event.type()==QEvent.FileOpen:
                backend=state['backend']
                if backend is None:
                    state['pending_file']=event.file()
                else:
                    backend.externalFileRequested.emit(event.url())
                return True
            return False
    file_events=FileEvents(app)
    app.installEventFilter(file_events)
    timer = QTimer()
    timer.setInterval(15)
    def begin():
        state['future'] = pool.submit(importlib.import_module, 'geotrace.workspace_app')
        timer.start()
    def poll():
        future = state['future']
        if future is None or not future.done():
            return
        timer.stop()
        try:
            module = future.result()
            if boot_engine.rootObjects():
                boot_engine.rootObjects()[0].setProperty('phase', 'Открываю рабочее пространство…')
            if diagnostic_profile:
                from PySide6.QtCore import QSettings
                profile=Path(diagnostic_profile.name)
                backend=module.WorkspaceBackend(recovery_dir=profile/'recovery',settings=QSettings(str(profile/'settings.ini'),QSettings.IniFormat))
            else:
                backend = module.WorkspaceBackend(args.sam_checkpoint)
            state['backend'] = backend
            engine = QQmlApplicationEngine()
            state['engine'] = engine
            engine.rootContext().setContextProperty('backend', backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).parent/'qml/Main.qml')))
            if not engine.rootObjects():
                raise RuntimeError('Не удалось открыть интерфейс GeoTrace.')
            state['ready'] = time.perf_counter()-started
            minimum_ms=1100 if boot_engine.rootObjects() and not args.self_check else 0
            remaining=max(0,minimum_ms-round((time.perf_counter()-state['boot_visible_at'])*1000))
            main_window=engine.rootObjects()[0]
            if remaining:main_window.hide()
            def reveal():
                for window in boot_engine.rootObjects():window.close()
                main_window.show()
                state['revealed']=True
            QTimer.singleShot(remaining,reveal)
            if args.startup_log:
                import json
                Path(args.startup_log).write_text(json.dumps(dict(splash_seconds=state['splash_shown'],
                    workspace_seconds=state['ready'],minimum_splash_seconds=minimum_ms/1000,
                    splash_hold_seconds=remaining/1000,version=__version__), indent=2))
            if args.auto:
                backend.mapLoaded.connect(lambda: QTimer.singleShot(0, backend.digitiseAll))
            path = state.get('pending_file') or args.map or args.project
            if path:
                QTimer.singleShot(0, lambda: backend.openFile(QUrl.fromLocalFile(str(Path(path).resolve()))))
            if args.screenshot:
                capture_timer.start()
            if args.self_check:
                from .selfcheck import start
                state['diagnostic_timer']=start(backend,engine,args.self_check,app,diagnostic_profile.name)
        except Exception as exc:
            state['error'] = str(exc)
            if sys.stderr:
                print(str(exc), file=sys.stderr)
            if args.self_check:
                import json
                Path(args.self_check).write_text(json.dumps(dict(ok=False,error=str(exc))),encoding='utf-8')
            app.exit(2)
    capture_timer = QTimer()
    capture_timer.setInterval(150)
    def capture():
        backend = state['backend']
        if backend is None:
            return
        if not state.get('revealed'):
            return
        if backend.data['error']:
            print(backend.data['status'], file=sys.stderr)
            app.exit(2)
        elif not backend.data['busy'] and (backend.result is not None or not args.auto):
            capture_timer.stop()
            def save():
                ok = state['engine'].rootObjects()[0].grabWindow().save(args.screenshot)
                app.exit(0 if ok else 3)
            QTimer.singleShot(150, save)
    capture_timer.timeout.connect(capture)
    timer.timeout.connect(poll)
    QTimer.singleShot(0, begin)
    if args.screenshot:
        QTimer.singleShot(300000, lambda: app.exit(4))
    result = app.exec()
    timer.stop()
    capture_timer.stop()
    pool.shutdown(wait=True, cancel_futures=True)
    backend = state['backend']
    if backend is not None:
        # Destroy QML while its context object is still alive. Python's final
        # garbage-collection order otherwise produces null-backend bindings.
        active_engine=state.get('engine')
        if active_engine is not None:
            import shiboken6
            shiboken6.delete(active_engine)
            state['engine']=None
        backend.timer.stop()
        backend.autosave_timer.stop()
        backend.basemap_loader.close()
        backend.close_details()
        backend.web_map.close()
        backend.pool.shutdown(wait=True, cancel_futures=True)
        backend.sample_lab = None
        backend.temp.cleanup()
    if diagnostic_profile:
        diagnostic_profile.cleanup()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
