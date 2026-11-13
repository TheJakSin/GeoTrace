import argparse
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from pathlib import Path
import sys
import tempfile
import importlib.util
import numpy as np
from scipy import ndimage

from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from shapely.geometry import Point
from shapely.strtree import STRtree

from .core import read_raster, segment, save_preview, save_overlay, export_gpkg, apply_proposal
from .prompts import propose, roi_at


def load_job(path, folder):
    raster = read_raster(path)
    save_preview(raster, Path(folder) / 'raster.png')
    return raster


def segment_job(raster, classes, minimum, folder):
    result = segment(raster, classes, minimum)
    save_overlay(result, Path(folder) / 'overlay.png')
    return result


def apply_job(raster, result, proposal, folder):
    output = apply_proposal(raster, result, proposal)
    save_overlay(output, Path(folder) / 'overlay.png')
    return output


class Backend(QObject):
    changed = Signal()
    mapLoaded = Signal()

    def __init__(self, checkpoint=None):
        super().__init__()
        self.temp = tempfile.TemporaryDirectory(prefix='geotrace-')
        self.pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))
        self.future = None
        self.raster = None
        self.result = None
        self.tree = None
        self.selected = -1
        self.revision = 0
        self.undo_stack = []
        self.points = []
        self.roi = None
        self.proposal = None
        self.checkpoint = checkpoint
        neural_available = bool(checkpoint and Path(checkpoint).is_file()
                                and importlib.util.find_spec('mobile_sam') and importlib.util.find_spec('torch'))
        self.data = dict(busy=False, dirty=False, filename='', metadata='', rasterUrl='', overlayUrl='',
                         selectionUrl='', width=1, height=1, count=0, colors=[], selection={},
                         hintUrl='', hintCount=0, proposalReady=False, touchesEdge=False,
                         neuralAvailable=neural_available, canUndo=False,
                         status='Откройте географически привязанную карту TIFF.', error=False)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @Property('QVariantMap', notify=changed)
    def state(self):
        return self.data

    def notify(self, **changes):
        self.data.update(changes)
        self.changed.emit()

    def url(self, name):
        return QUrl.fromLocalFile(str(Path(self.temp.name) / name)).toString() + f'?v={self.revision}'

    def start(self, kind, func, *args):
        if self.data['busy']:
            return
        self.kind = kind
        self.future = self.pool.submit(func, *args)
        self.notify(busy=True, error=False,
                    status={'load': 'Читаю карту…', 'segment': 'Выделяю цветовые области и строю полигоны…',
                            'prompt': 'Уточняю область по подсказкам…', 'apply': 'Принимаю область…',
                            'export': 'Сохраняю GeoPackage…'}[kind])

    @Slot(QUrl)
    def openFile(self, url):
        self.start('load', load_job, url.toLocalFile(), self.temp.name)

    @Slot(int, int)
    def run(self, classes, minimum):
        if self.raster is not None:
            self.start('segment', segment_job, self.raster, classes, minimum, self.temp.name)

    @Slot(QUrl)
    def exportFile(self, url):
        if self.result is not None:
            path = url.toLocalFile()
            if not path.lower().endswith('.gpkg'):
                path += '.gpkg'
            self.start('export', export_gpkg, self.result, path)

    @Slot()
    def poll(self):
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        try:
            output = future.result()
            self.revision += 1
            if self.kind == 'load':
                self.raster, self.result, self.tree = output, None, None
                self.selected = -1
                self.undo_stack.clear()
                self.points, self.roi, self.proposal = [], None, None
                h, w = output.valid.shape
                sw, sh = output.source_size
                self.notify(filename=Path(output.path).name, width=w, height=h,
                            metadata=f'{output.crs_label}  ·  исходник {sw} × {sh} px  ·  обработка {w} × {h} px',
                            rasterUrl=self.url('raster.png'), overlayUrl='', selectionUrl='', selection={},
                            count=0, colors=[], dirty=False, hintUrl='', hintCount=0, proposalReady=False,
                            touchesEdge=False, canUndo=False, status='Карта загружена. Можно выделить области.')
                self.mapLoaded.emit()
            elif self.kind in ('segment', 'apply'):
                if self.kind == 'segment':
                    self.undo_stack.clear()
                else:
                    self.undo_stack.append(('result', self.result))
                    self.undo_stack = self.undo_stack[-12:]
                self.set_result(output)
                self.points, self.roi, self.proposal = [], None, None
                self.notify(hintUrl='', hintCount=0, proposalReady=False, touchesEdge=False,
                            status=f'{len(output.features)} полигонов. Проверьте границы перед экспортом.')
            elif self.kind == 'prompt':
                self.proposal = output
                self.draw_hints()
                self.notify(proposalReady=True, touchesEdge=output.touches_edge,
                            status='Предварительное выделение. Уточните подсказки или примите область.' +
                            (' Внимание: выделение достигает рамки анализа.' if output.touches_edge else ''))
            else:
                self.notify(dirty=False, status=f'Сохранено: {output}')
        except Exception as exc:
            self.notify(status=str(exc), error=True)
        finally:
            self.notify(busy=False)

    def set_result(self, output):
        self.result = output
        self.tree = STRtree([f['geometry'] for f in output.features]) if output is not None else None
        self.selected = -1
        colors = []
        if output is not None:
            for i, rgb in enumerate(output.colors):
                colors.append({'label': f'Группа {i + 1}', 'color': '#%02x%02x%02x' % tuple(rgb),
                               'count': sum(f['properties']['class_id'] == i+1 for f in output.features)})
        self.notify(overlayUrl=self.url('overlay.png') if output is not None else '', selectionUrl='',
                    selection={}, dirty=True, count=len(output.features) if output else 0, colors=colors,
                    canUndo=bool(self.undo_stack))

    @Slot(float, float, int, int)
    def addHint(self, x, y, label, size):
        if self.raster is None or self.data['busy']:
            return
        x, y = int(x), int(y)
        h, w = self.raster.valid.shape
        if not (0 <= x < w and 0 <= y < h) or not self.raster.valid[y,x]:
            self.notify(status='Поставьте точку внутри непрозрачной части карты.', error=True)
            return
        if self.roi is None:
            if label != 1:
                self.notify(status='Сначала укажите точку «Включить».', error=True)
                return
            self.roi = roi_at(self.raster, x, y, size)
        x0,y0,x1,y1 = self.roi
        if not (x0 <= x < x1 and y0 <= y < y1):
            self.notify(status='Точка вне рамки анализа. Начните новую область с большей рамкой.', error=True)
            return
        if len(self.points) >= 64:
            self.notify(status='Достигнут предел: 64 подсказки.', error=True)
            return
        self.points = [p for p in self.points if p[:2] != (x,y)]
        self.points.append((x,y,label))
        self.proposal = None
        self.draw_hints()
        self.notify(proposalReady=False, touchesEdge=False, error=False,
                    status='Подсказка добавлена. Нажмите «Уточнить область».')

    @Slot()
    def removeHint(self):
        if not self.points or self.data['busy']:
            return
        self.points.pop()
        self.proposal = None
        if not self.points:
            self.roi = None
        self.draw_hints()
        self.notify(proposalReady=False, touchesEdge=False)

    @Slot()
    def clearHints(self):
        if self.data['busy']:
            return
        self.points, self.roi, self.proposal = [], None, None
        self.notify(hintUrl='', hintCount=0, proposalReady=False, touchesEdge=False)

    @Slot()
    def invalidateProposal(self):
        if self.data['busy']:
            return
        self.proposal = None
        self.draw_hints()
        self.notify(proposalReady=False, touchesEdge=False)

    def draw_hints(self):
        if self.raster is None:
            return
        h,w = self.raster.valid.shape
        rgba = np.zeros((h,w,4),dtype=np.uint8)
        if self.proposal is not None:
            mask = self.proposal.mask
            rgba[mask] = [255,164,48,105]
            edge = mask ^ ndimage.binary_erosion(mask)
            rgba[edge] = [255,126,0,255]
        image = Image.fromarray(rgba)
        draw = ImageDraw.Draw(image)
        if self.roi:
            x0,y0,x1,y1 = self.roi
            draw.rectangle((x0,y0,x1-1,y1-1),outline=(255,255,255,255),width=3)
            draw.rectangle((x0+1,y0+1,x1-2,y1-2),outline=(35,92,125,255),width=1)
        for x,y,label in self.points:
            draw.ellipse((x-6,y-6,x+6,y+6),fill=(5,150,90,255) if label else (220,50,60,255),
                         outline='white',width=2)
            draw.line((x-3,y,x+3,y),fill='white',width=1)
            if label: draw.line((x,y-3,x,y+3),fill='white',width=1)
        image.save(Path(self.temp.name) / 'hints.png')
        self.revision += 1
        self.notify(hintUrl=self.url('hints.png') if self.points else '', hintCount=len(self.points))

    @Slot(int, int)
    def runPrompt(self, method, tolerance):
        if self.raster is None or self.data['busy']:
            return
        if not self.points or not any(p[2] == 1 for p in self.points):
            self.notify(status='Поставьте точку «Включить» на нужной области.', error=True)
            return
        self.proposal = None
        self.draw_hints()
        self.notify(proposalReady=False, touchesEdge=False)
        self.start('prompt', propose, self.raster, self.roi, list(self.points),
                   'mobilesam' if method == 1 else 'color', tolerance, self.checkpoint)

    @Slot()
    def acceptPrompt(self):
        if self.proposal is not None:
            self.start('apply', apply_job, self.raster, self.result, self.proposal, self.temp.name)

    @Slot(float, float)
    def selectAt(self, x, y):
        if self.tree is None or self.data['busy']:
            return
        gx, gy = self.raster.transform * (x, y)
        matches = self.tree.query(Point(gx, gy), predicate='intersects')
        self.selected = int(matches[0]) if len(matches) else -1
        self.refresh_selection()

    def refresh_selection(self):
        if self.selected < 0:
            self.notify(selection={}, selectionUrl='')
            return
        feature = self.result.features[self.selected]
        inverse = ~self.raster.transform
        image = Image.new('RGBA', (self.data['width'], self.data['height']))
        draw = ImageDraw.Draw(image)
        rings = [feature['geometry'].exterior, *feature['geometry'].interiors]
        for ring in rings:
            points = [inverse * point for point in ring.coords]
            draw.line(points, fill=(255, 255, 255, 255), width=6)
            draw.line(points, fill=(0, 102, 255, 255), width=3)
        image.save(Path(self.temp.name) / 'selection.png')
        self.revision += 1
        self.notify(selection=dict(feature['properties']), selectionUrl=self.url('selection.png'))

    @Slot(str, str, str)
    def updateAttributes(self, index, name, note):
        if self.selected < 0 or self.data['busy']:
            return
        props = self.result.features[self.selected]['properties']
        self.undo_stack.append(('attribute', (self.selected, props.copy())))
        self.undo_stack = self.undo_stack[-12:]
        props.update(unit_index=index, unit_name=name, note=note, review='edited')
        self.notify(dirty=True, canUndo=True, status='Атрибуты изменены. Сохраните новую версию GeoPackage.')
        self.refresh_selection()

    @Slot()
    def undo(self):
        if not self.undo_stack or self.data['busy']:
            return
        kind, previous = self.undo_stack.pop()
        if kind == 'attribute':
            self.selected, properties = previous
            self.result.features[self.selected]['properties'] = properties
            self.refresh_selection()
        else:
            if previous is not None:
                save_overlay(previous, Path(self.temp.name) / 'overlay.png')
            self.revision += 1
            self.set_result(previous)
        self.notify(dirty=True, canUndo=bool(self.undo_stack), status='Последнее изменение отменено.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--map')
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--sam-checkpoint', help='Optional local MobileSAM vit_t weights; no automatic download')
    parser.add_argument('--screenshot', help='Developer smoke check: save window after processing and exit')
    args = parser.parse_args()
    QQuickStyle.setStyle('Basic')
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName('GeoTrace')
    app.setOrganizationName('GeoTrace')
    backend = Backend(args.sam_checkpoint)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml' / 'Main.qml')))
    if not engine.rootObjects():
        backend.pool.shutdown(wait=False, cancel_futures=True)
        return 1
    if args.auto:
        backend.mapLoaded.connect(lambda: QTimer.singleShot(0, lambda: backend.run(7, 80)))
    if args.map:
        QTimer.singleShot(0, lambda: backend.openFile(QUrl.fromLocalFile(str(Path(args.map).resolve()))))
    if args.screenshot:
        check = QTimer()
        check.setInterval(300)
        def capture():
            if backend.data['error']:
                print(backend.data['status'], file=sys.stderr)
                app.exit(2)
            elif not backend.data['busy'] and (backend.result is not None or not args.auto):
                check.stop()
                def save():
                    ok = engine.rootObjects()[0].grabWindow().save(args.screenshot)
                    app.exit(0 if ok else 3)
                QTimer.singleShot(500, save)
        check.timeout.connect(capture)
        check.start()
        QTimer.singleShot(120000, lambda: app.exit(4))
    result = app.exec()
    backend.pool.shutdown(wait=True, cancel_futures=True)
    del engine
    backend.temp.cleanup()
    return result


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
