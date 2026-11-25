import argparse
import copy
import csv
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from pathlib import Path
import sys
import tempfile
import importlib.util
from datetime import datetime
import time
import cv2
import uuid
import shutil
import json
import numpy as np
from scipy import ndimage

from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, Property, QTimer, QUrl, QStandardPaths, QSettings, Signal, Slot, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from shapely.geometry import Point
from shapely.strtree import STRtree

from .core import read_raster, segment, save_preview, save_overlay, export_gpkg, apply_proposal, Result
from .prompts import propose, roi_at
from .project import load_project, save_project, VIEW_DEFAULTS, validate_view
from .region import read_kml, set_area, clip_result, save_area_preview
from .basemap import BasemapLoader, viewport_spec, CONTEXT_MARGIN
from .basemap_config import DEFAULT_YANDEX_TILES_API_KEY, DEFAULT_YANDEX_JS_API_KEY
from .webmap import WebMapServer
from .attributes import change_schema, values_for, defaults, SUGGESTED_FIELDS
from .table import PolygonTable, plan_bulk, editable_fields, display
from .boundary_drag import EdgeIndex, apply_drag
from .editing import edit_geometry, draw_polygon, move_vertex, transform_geometry, lasso_indices
from .automatic import preserve_work, protected_feature
from .palette import sample_fill, split_labels
from .quality import prepare_job as prepare_quality_job, quality_job, Cancelled
from .release_info import version_label

if QGuiApplication.instance() is None:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)


def load_job(path, folder):
    raster = read_raster(path)
    save_preview(raster, Path(folder) / 'raster.png')
    return raster


def area_job(raster, result, geometry, name, folder):
    raster = set_area(raster, geometry, name)
    result = clip_result(result, raster) if result is not None else None
    save_preview(raster, Path(folder)/'raster.png')
    if result is not None:
        save_overlay(result, Path(folder)/'overlay.png')
    return raster, result


def segment_job(raster, classes, minimum, folder, fields=None, previous=None):
    from .smoothing import smooth_automatic_result
    result = preserve_work(change_schema(smooth_automatic_result(segment(raster, classes, minimum)), fields or []), previous)
    save_overlay(result, Path(folder) / 'overlay.png')
    return result


def auto_job(raster, previous, classes, minimum, profile, folder):
    from .smoothing import smooth_automatic_result
    result = preserve_work(smooth_automatic_result(segment(raster, classes, minimum, profile)), previous)
    save_overlay(result, Path(folder) / 'auto-preview.png')
    return result


def apply_job(raster, result, proposal, folder):
    output = apply_proposal(raster, result, proposal)
    save_overlay(output, Path(folder) / 'overlay.png')
    return output


def open_project_job(path, folder):
    project = load_project(path)
    save_preview(project.raster, Path(folder) / 'raster.png')
    if project.result is not None:
        save_overlay(project.result, Path(folder) / 'overlay.png')
    return project


def drag_job(result, drag, dx, dy, folder):
    output = apply_drag(result,drag,dx,dy)
    save_overlay(output,Path(folder)/'edit-preview.png')
    return output


def edit_job(result, object_ids, operation, points, folder, raster=None, draw_class=None, vector_preview=False):
    if operation == 'draw':
        output = draw_polygon(raster, result, points, draw_class)
    elif operation == 'vertex':
        if len(object_ids) != 1:
            raise ValueError('Выберите один полигон для перемещения вершины.')
        output = move_vertex(result, object_ids[0], points)
    else:
        output = edit_geometry(result, object_ids, operation, points)
    save_overlay(output, Path(folder) / 'edit-preview.png', outlines=not vector_preview)
    old = {f['properties']['object_id']:f for f in result.features} if result else {}
    changed_ids = [f['properties']['object_id'] for f in output.features
           if f['properties']['object_id'] not in old or not f['geometry'].equals_exact(
               old[f['properties']['object_id']]['geometry'], 0)]
    ids = [oid for oid in object_ids if oid in changed_ids] + [oid for oid in changed_ids if oid not in object_ids]
    lookup = {f['properties']['object_id']: f for f in output.features}
    h, w = output.raster.valid.shape
    image = Image.new('RGBA', (w,h))
    draw = ImageDraw.Draw(image)
    inverse = ~output.raster.transform
    for number, oid in enumerate(ids):
        if oid not in lookup:
            continue
        geometry = lookup[oid]['geometry']
        for ring in [geometry.exterior, *geometry.interiors]:
            points = [inverse * point for point in ring.coords]
            draw.line(points, fill='white', width=6)
            draw.line(points, fill=(0,102,255,255) if number == 0 else (235,126,30,255), width=3)
    image.save(Path(folder) / 'edit-selection.png')
    return output


class Backend(QObject):
    changed = Signal()
    mapLoaded = Signal()
    projectSaved = Signal()
    focusPolygon = Signal(float, float, float, float)
    palettePrepared = Signal()
    areaPrepared = Signal()
    basemapConfigurationChanged = Signal()
    webMapConfigurationChanged = Signal()
    compareShortcut = Signal()

    def __init__(self, checkpoint=None, recovery_dir=None, settings=None):
        super().__init__()
        self._closing=False
        from PySide6.QtQml import QQmlEngine
        QQmlEngine.setObjectOwnership(self,QQmlEngine.CppOwnership)
        self.temp = tempfile.TemporaryDirectory(prefix='geotrace-')
        self.pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))
        self.future = None
        self.compare_enabled = False
        self.shift_pending = False
        self.shift_used = False
        self.settings = settings if settings is not None else QSettings('GeoTrace', 'GeoTrace')
        self.yandex_key = self.settings.value('basemap/yandexTilesApiKey', DEFAULT_YANDEX_TILES_API_KEY, type=str)
        self.web_key = self.settings.value('webmap/yandexJsApiKey', DEFAULT_YANDEX_JS_API_KEY, type=str)
        self.web_map = WebMapServer(self.web_key)
        self.destroyed.connect(self.web_map.close)
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation))/'basemaps'/'eox2016'
        self.basemap_loader = BasemapLoader(cache, self.temp.name)
        self.basemap_spec = None
        self.destroyed.connect(self.basemap_loader.close)
        self.raster = None
        self.pending_areas = []
        self.view = dict(VIEW_DEFAULTS)
        self.view_dirty = False
        self.result = None
        self.edge_index = None
        self.edge_drag = None
        self.tree = None
        self.selected = -1
        self.table = PolygonTable(self)
        self.pending_bulk = None
        self.table.statsChanged.connect(self.table_stats)
        self.edit_selected = []
        self.edit_points = []
        self.edit_preview = None
        self.auto_preview = None
        self.auto_revision = None
        self.palette_samples = []
        self.review_log = []
        self.review_started = None
        self.review_sites = []
        self.review_index = -1
        self.quality_token = ''
        self.quality_groups = []
        self.quality_original = []
        self.palette_labels = None
        self.prepare_after_load = False
        self.prepare_on_import = True
        self.edit_tool = 5
        self.vertex_source = None
        self.revision = 0
        self.undo_stack = []
        self.redo_stack = []
        self.restoring_history = False
        self.points = []
        self.roi = None
        self.proposal = None
        self.checkpoint = checkpoint
        self.project_path = ''
        self.edit_revision = 0
        self.autosaved_revision = 0
        self.autosave_paused = False
        self.recovery_dir = Path(recovery_dir) if recovery_dir is not None else Path(
            QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / 'recovery'
        self.recovery_path = self.recovery_dir / (uuid.uuid4().hex + '.geotrace')
        self.restored_path = None
        self.save_target = ''
        neural_available = bool(checkpoint and Path(checkpoint).is_file()
                                and importlib.util.find_spec('mobile_sam') and importlib.util.find_spec('torch'))
        self.data = dict(attributeEpoch=0,busy=False, dirty=False, filename='', metadata='', rasterUrl='', overlayUrl='',
                         webMapRevision=0, webMapUrl='', webMapAvailable=importlib.util.find_spec('PySide6.QtWebEngineQuick') is not None,
                         webMapKeyConfigured=bool(self.web_key), webMapKeyError='', webMapStatus='Карта ещё не открыта',
                         yandexKeyConfigured=bool(self.yandex_key), yandexKeyError='',
                         basemapUrl='', basemapRect={}, basemapCredit='', basemapNote='', basemapLoading=False, basemapError='',
                         areaName='', areaUrl='', areaDraftUrl='', areaDraftRasterUrl='', areaOptions=[], layerSettings=dict(self.view), basemapMargin=CONTEXT_MARGIN,
                         autoReady=False, autoPreviewUrl='', autoSummary='',
                         qualityReady=False, qualityPalette=[], qualityNote='', qualityProgress=0,
                         qualityETA='', paletteMessage='', reviewLogged=0, reviewActive=False, reviewCount=0, reviewPosition=0, reviewMarker={}, reviewDecision='', reviewText='', reviewTool=-1, qualityStage='', qualityBusy=False, qualityCancelPending=False,
                         paletteUrl='', qualityEvidenceUrl='', qualityInfo='',
                         selectionUrl='', vertexPoints=[], vertexSource={}, editSelected=[], editPoints=[], editPreviewUrl='', editSelectionUrl='', geometryReady=False, width=1, height=1, count=0, colors=[], selection={},
                         hintUrl='', hintCount=0, proposalReady=False, touchesEdge=False,
                         neuralAvailable=neural_available, canUndo=False, canRedo=False, projectPath='', projectName='',
                         suggestions=SUGGESTED_FIELDS, autosaveStatus='', recoveries=self.recovery_items(), fields=[], schemaError='', tableColumns=self.table.columns, tableMatched=0, tableTotal=0, tableSelected=0,
                         tableSort='object_id', tableDescending=False, bulkFields=editable_fields(None), bulkSummary='',
                         status='Откройте географически привязанную карту TIFF.', error=False)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(5000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

    @Slot(QObject, result=bool)
    def isTextInput(self, item):
        return item is not None and (item.inherits('QQuickTextInput') or item.inherits('QQuickTextEdit') or item.inherits('QQuickComboBox') or item.inherits('QQuickSpinBox'))

    @Slot()
    def prepareClose(self):
        self._closing=True
        self.compare_enabled=False
        self.timer.stop();self.autosave_timer.stop()

    @Slot(bool)
    def setCompareShortcutEnabled(self,enabled):
        self.compare_enabled=enabled

    @Slot()
    def triggerCompareShortcut(self):
        if self.compare_enabled:self.compareShortcut.emit()

    @Property(QObject, constant=True)
    def polygonTable(self):
        return self.table

    @Property(str, constant=True)
    def releaseLabel(self):
        return version_label()

    @Property('QVariantMap', notify=changed)
    def state(self):
        return self.data

    def notify(self, **changes):
        import shiboken6
        if getattr(self,'_closing',False) or not shiboken6.isValid(self):return
        if any(key in changes for key in ('rasterUrl', 'overlayUrl', 'areaUrl')):
            changes['webMapRevision'] = self.data.get('webMapRevision', 0)+1
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
                            'auto_preview': 'Готовлю выделение, сохраняя ручные правки…',
                            'quality_prepare': 'Анализирую заливки и границы…',
                            'quality_segment': 'Согласую цветовые области и исходные границы…',
                            'prompt': 'Уточняю область по подсказкам…', 'apply': 'Принимаю область…',
                            'merge_direct': 'Объединяю выбранные полигоны…', 'drag': 'Сохраняю границу…', 'geometry': 'Проверяю геометрию правки…', 'export': 'Сохраняю GeoPackage…', 'project_load': 'Открываю проект…',
                            'area_load': 'Читаю контуры KML…', 'area_apply': 'Применяю область обработки…',
                            'project_save': 'Сохраняю проект…', 'autosave': 'Создаю копию для восстановления…'}[kind])

    @Slot(QUrl)
    def openFile(self, url):
        if self.data['busy']:
            return
        path = url.toLocalFile()
        if path.lower().endswith('.geotrace'):
            self.prepare_after_load = False
            self.load_target, self.is_recovery = path, False
            self.start('project_load', open_project_job, path, self.temp.name)
        else:
            self.prepare_after_load = self.prepare_on_import
            self.start('load', load_job, path, self.temp.name)

    @Slot(QUrl)
    def loadArea(self, url):
        if self.raster is not None and not self.data['busy']:
            self.start('area_load', read_kml, url.toLocalFile(), self.raster)

    def refresh_area(self):
        save_area_preview(self.raster, self.raster.area, Path(self.temp.name)/'area.png')
        self.notify(areaName=(self.raster.area_name or 'Контур обработки') if self.raster.area is not None else '', areaUrl=self.url('area.png') if self.raster.area is not None else '',
                    areaDraftUrl='', areaDraftRasterUrl='', areaOptions=[])

    @Slot(int)
    def previewArea(self, index):
        if self.raster is None or self.data['busy'] or not 0 <= index < len(self.pending_areas):
            return
        try:
            name, geometry = self.pending_areas[index]
            candidate = set_area(self.raster, geometry, name)
            save_preview(candidate, Path(self.temp.name)/'area-draft-raster.png')
            save_area_preview(candidate, candidate.area, Path(self.temp.name)/'area-draft.png')
            self.revision += 1
            self.notify(areaDraftUrl=self.url('area-draft.png'), areaDraftRasterUrl=self.url('area-draft-raster.png'), error=False,
                        status='Предпросмотр обрезки: останется карта внутри контура KML.')
        except ValueError as exc:
            self.notify(areaDraftUrl='', areaDraftRasterUrl='', status=str(exc), error=True)

    @Slot()
    def dismissArea(self):
        self.pending_areas = []
        self.notify(areaDraftUrl='', areaDraftRasterUrl='', areaOptions=[])

    def start_area_change(self, geometry, name):
        self.area_snapshot = (self.raster, self.result, list(self.points), self.roi)
        self.start('area_apply', area_job, self.raster, self.result, geometry, name, self.temp.name)

    @Slot(int)
    def applyArea(self, index):
        if self.raster is not None and not self.data['busy'] and 0 <= index < len(self.pending_areas):
            name, geometry = self.pending_areas[index]
            self.start_area_change(geometry, name)

    @Slot()
    def clearArea(self):
        if self.raster is not None and self.raster.area is not None and not self.data['busy']:
            self.start_area_change(None, '')

    @Slot(str, 'QVariant')
    def setLayer(self, key, value):
        if key not in VIEW_DEFAULTS or self.data['busy']:
            return
        try:
            updated = validate_view(dict(self.view, **{key: value}))
        except ValueError:
            return
        if updated == self.view:
            return
        changed_source = updated['basemap_source'] != self.view['basemap_source']
        activated = updated['basemap_visible'] and not self.view['basemap_visible']
        if activated and updated['raster_opacity'] == 1:
            updated['raster_opacity'] = .65
        self.view = updated
        if changed_source or not updated['basemap_visible']:
            self.reset_basemap()
        if self.raster is not None:
            self.view_dirty = True
            self.notify(dirty=True, autosaveStatus='Ожидает автосохранения')
        self.notify(layerSettings=dict(self.view))

    def reset_basemap(self):
        self.basemap_loader.request(None)
        self.basemap_spec = None
        self.notify(basemapUrl='', basemapRect={}, basemapCredit='', basemapNote='',
                    basemapLoading=False, basemapError='')

    @Slot(float, float, float, float, int, int)
    def requestBasemap(self, x, y, width, height, pixels_w, pixels_h):
        if self.raster is None or not self.view['basemap_visible']:
            return
        try:
            values = np.asarray([x, y, width, height, pixels_w, pixels_h])
            if not np.isfinite(values).all() or width <= 0 or height <= 0 or pixels_w <= 0 or pixels_h <= 0:
                return
            spec = viewport_spec(self.raster, self.view, x, y, width, height, pixels_w, pixels_h)
            if spec is None:
                return
            if spec['source'] == 'yandex':
                spec['api_key'] = self.yandex_key
            if spec == self.basemap_spec:
                return
            self.basemap_spec = spec
            self.basemap_loader.request(spec)
            self.notify(basemapLoading=True, basemapError='')
            self.poll_basemap()
        except ValueError as exc:
            self.notify(basemapLoading=False, basemapError=str(exc))

    @Slot(str, result=bool)
    def setYandexKey(self, key):
        key = key.strip()
        if len(key) > 256 or any(c.isspace() or ord(c) < 32 for c in key):
            self.notify(yandexKeyError='Введите ключ без пробелов и переносов строк.')
            return False
        self.settings.setValue('basemap/yandexTilesApiKey', key)
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            self.notify(yandexKeyError='Не удалось сохранить ключ в настройках приложения.')
            return False
        self.activate_yandex_key(key)
        return True

    def activate_yandex_key(self, key):
        self.yandex_key = key
        self.reset_basemap()
        self.notify(yandexKeyConfigured=bool(key), yandexKeyError='')
        self.basemapConfigurationChanged.emit()

    @Slot(result=bool)
    def resetYandexKey(self):
        self.settings.remove('basemap/yandexTilesApiKey')
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            self.notify(yandexKeyError='Не удалось сохранить настройки приложения.')
            return False
        self.activate_yandex_key(DEFAULT_YANDEX_TILES_API_KEY)
        return True

    @Slot()
    def refreshBasemap(self):
        if self.basemap_spec is not None and self.view['basemap_visible']:
            self.basemap_loader.request(self.basemap_spec, refresh=True)
            self.notify(basemapLoading=True, basemapError='')

    @Slot(QUrl)
    def openBasemapFile(self, url):
        if self.raster is None or self.data['busy']:
            return
        path = url.toLocalFile()
        if not path:
            return
        self.view = validate_view(dict(self.view, basemap_source='local', basemap_file=path, basemap_visible=True))
        if self.view['raster_opacity'] == 1:
            self.view['raster_opacity'] = .65
        self.view_dirty = True
        self.reset_basemap()
        self.notify(layerSettings=dict(self.view), dirty=True, autosaveStatus='Ожидает автосохранения')

    def poll_basemap(self):
        output = self.basemap_loader.poll()
        if output is None:
            return
        if 'error' in output:
            self.notify(basemapLoading=False, basemapError=output['error'])
        else:
            x, y, w, h = output['rect']
            self.notify(basemapUrl=QUrl.fromLocalFile(output['path']).toString(),
                        basemapRect=dict(x=x, y=y, w=w, h=h), basemapCredit=output['credit'],
                        basemapNote=output['note'], basemapLoading=False, basemapError='')

    @Slot()
    def openWebMap(self):
        self.refreshWebMapContext()
        try:
            self.notify(webMapUrl=self.web_map.start(), webMapStatus='Подключение к Яндекс Картам…')
        except OSError:
            self.notify(webMapStatus='Не удалось открыть вкладку карты. Повторите попытку.')

    @Slot()
    def refreshWebMapContext(self):
        try:
            self.web_map.set_project(self.raster, self.result)
        except (ValueError, OSError):
            self.web_map.set_project(None, None)

    @Slot(str)
    def reportWebMap(self, status):
        messages = dict(loading='Подключение к Яндекс Картам…', ready='Карта открыта',
                        error='Проверьте интернет и доступ ключа к JavaScript API Яндекс Карт.')
        if status in messages and self.data['webMapStatus'] != messages[status]:
            self.notify(webMapStatus=messages[status])

    @Slot(str, result=bool)
    def setWebMapKey(self, key):
        key = key.strip()
        if not key or len(key) > 256 or any(c.isspace() or ord(c) < 32 for c in key):
            self.notify(webMapKeyError='Введите ключ JavaScript API без пробелов и переносов строк.')
            return False
        self.settings.setValue('webmap/yandexJsApiKey', key)
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            self.notify(webMapKeyError='Не удалось сохранить настройку.')
            return False
        self.activate_web_key(key)
        return True

    def activate_web_key(self, key):
        self.web_key = key
        self.web_map.update(key=key)
        self.notify(webMapKeyConfigured=bool(key), webMapKeyError='')
        self.webMapConfigurationChanged.emit()

    @Slot(result=bool)
    def resetWebMapKey(self):
        self.settings.remove('webmap/yandexJsApiKey')
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            self.notify(webMapKeyError='Не удалось сохранить настройку.')
            return False
        self.activate_web_key(DEFAULT_YANDEX_JS_API_KEY)
        return True

    def recovery_items(self):
        if not self.recovery_dir.exists():
            return []
        items = []
        for path in self.recovery_dir.glob('*.geotrace'):
            if path != self.recovery_path:
                try:
                    modified = path.stat().st_mtime
                    items.append(dict(path=str(path), label='Рабочая копия · ' +
                                      datetime.fromtimestamp(modified).strftime('%d.%m.%Y %H:%M'), modified=modified))
                except OSError:
                    pass
        return sorted(items, key=lambda item: item['modified'], reverse=True)

    def mark_dirty(self):
        if not self.restoring_history and self.redo_stack:
            self.redo_stack.clear()
            self.notify(canRedo=False)
        if self.auto_preview is not None:
            self.reset_review()
        self.clear_auto_preview()
        self.edit_revision += 1
        self.notify(attributeEpoch=self.edit_revision, dirty=True, autosaveStatus='Ожидает автосохранения')

    def remove_active_recovery(self, include_restored=False):
        paths = [self.recovery_path]
        if include_restored and self.restored_path:
            paths.append(self.restored_path)
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # A stale backup is preferable to a failed successful save.
        self.restored_path = None

    @Slot()
    def discardRecovery(self):
        self.remove_active_recovery(include_restored=True)

    @Slot(str)
    def restoreRecovery(self, path):
        if self.data['busy'] or self.raster is not None:
            return
        if path not in {item['path'] for item in self.data['recoveries']}:
            return
        self.load_target, self.is_recovery = path, True
        self.start('project_load', open_project_job, path, self.temp.name)

    @Slot(QUrl)
    def saveProject(self, url):
        if self.raster is None or self.data['busy']:
            return
        path = url.toLocalFile() or self.project_path
        if not path:
            return
        if not path.lower().endswith('.geotrace'):
            path += '.geotrace'
        self.save_target = str(Path(path).resolve())
        self.start('project_save', save_project, self.save_target, self.raster,
                   self.result, list(self.points), self.roi, '', dict(self.view))

    @Slot(bool)
    def pauseAutosave(self, paused):
        self.autosave_paused = paused

    @Slot()
    def autosave(self):
        if self.autosave_paused or self.raster is None or self.data['busy'] or (self.edit_revision == self.autosaved_revision and not self.view_dirty):
            return
        self.start('autosave', save_project, str(self.recovery_path), self.raster,
                   self.result, list(self.points), self.roi, self.project_path, dict(self.view))

    @Slot(int, int)
    def run(self, classes, minimum):
        if self.raster is not None:
            self.start('segment', segment_job, self.raster, classes, minimum, self.temp.name,
                       self.result.fields if self.result else [], self.result)

    def clear_auto_preview(self):
        self.auto_preview = None
        self.auto_revision = None
        self.notify(autoReady=False,autoPreviewUrl='',autoSummary='',qualityInfo='')

    def reset_review(self):
        self.review_started = None
        self.review_sites = []
        self.review_index = -1
        self.notify(reviewCount=0,reviewPosition=0,reviewMarker={},reviewDecision='',reviewText='',reviewTool=-1,reviewActive=False)

    def reset_quality(self):
        self.reset_review()
        self.palette_samples = []
        self.base_palette = []
        self.sample_lab = None
        self.notify(paletteMessage='')
        self.quality_token = ''
        self.quality_groups = []
        self.quality_original = []
        self.palette_labels = None
        self.notify(qualityReady=False, qualityPalette=[], qualityNote='', paletteUrl='', qualityEvidenceUrl='')

    def start_quality(self, kind, func, *args):
        for name in ('quality-cancel', 'quality-progress.json'):
            (Path(self.temp.name)/name).unlink(missing_ok=True)
        self.quality_started = time.monotonic()
        self.notify(qualityETA='Оцениваю время…',qualityBusy=True, qualityProgress=0, qualityStage='Подготовка…', qualityCancelPending=False)
        self.start(kind, func, *args)

    @Slot()
    def prepareQuality(self):
        if self.raster is None or self.data['busy']:
            return
        self.clear_auto_preview()
        self.reset_quality()
        self.quality_token = uuid.uuid4().hex
        self.start_quality('quality_prepare', prepare_quality_job, self.raster, self.quality_token, self.temp.name)

    @Slot()
    def previewQuality(self):
        if self.raster is None or self.data['busy']:
            return
        if not self.data['qualityReady']:
            self.prepareQuality()
            return
        if not any(self.quality_groups):
            self.notify(status='Оставьте хотя бы одну целевую заливку.', error=True)
            return
        self.clear_auto_preview()
        self.reset_review()
        self.auto_revision = self.edit_revision
        self.start_quality('quality_segment', quality_job, self.raster, self.result, self.quality_token,
                           list(self.quality_groups), self.temp.name,list(self.palette_samples),list(self.quality_original))

    @Slot()
    def cancelQuality(self):
        if self.data['qualityBusy']:
            (Path(self.temp.name)/'quality-cancel').touch()
            self.notify(qualityCancelPending=True, qualityStage='Останавливаю после текущего этапа…')

    def update_palette(self):
        self.reset_review()
        palette=[]
        for item, target in zip(self.quality_original,self.quality_groups):
            palette.append(dict(item, enabled=target>0, target=target,
                                label=item['label'] if target in (0,item['id']) else f"Заливка {item['id']} → {target}"))
        self.clear_auto_preview()
        self.notify(qualityPalette=palette)

    @Slot(int, bool)
    def includePalette(self, ident, enabled):
        if self.data['busy'] or not 1<=ident<=len(self.quality_groups):
            return
        self.quality_groups[ident-1]=ident if enabled else 0
        self.update_palette()
        self.focusPalette(ident)

    @Slot(int, int)
    def mergePalette(self, source, target):
        if self.data['busy'] or not (1<=source<=len(self.quality_groups) and 1<=target<=len(self.quality_groups)) or source==target:
            return
        target_id=self.quality_groups[target-1] or target
        source_id=self.quality_groups[source-1] or source
        self.quality_groups=[target_id if v==source_id else v for v in self.quality_groups]
        self.quality_groups[source-1]=target_id
        self.quality_groups[target-1]=target_id
        self.update_palette()
        self.focusPalette(target)

    @Slot(int,int,result=int)
    def samplePalette(self,x,y):
        if self.data['busy'] or not self.data['qualityReady']:
            return 0
        try:
            if len(self.quality_groups)>=64:
                raise ValueError('Достигнут предел в 64 цветовые группы.')
            if self.sample_lab is None:
                # The working overview is bounded (~31 MB). An owned array
                # releases the file immediately, including on Windows.
                self.sample_lab = np.load(Path(self.temp.name)/'palette-lab.npy',allow_pickle=False)
            ident = len(self.quality_groups)+1
            sample = sample_fill(self.sample_lab,self.palette_labels,x,y,ident)
            labels = split_labels(self.sample_lab,self.palette_labels,sample)
            counts = np.bincount(labels.ravel(),minlength=ident+1)
            if min(counts[ident],counts[sample['parent']])<4 or labels[y,x]!=ident:
                raise ValueError('Оттенок не образует отдельной заливки. Укажите однородное место.')
            def sample_rgb(key):
                center = np.array(sample[key],np.float32)
                center[0] /= .4
                color = center.clip(0,255).astype(np.uint8).reshape(1,1,3)
                return cv2.cvtColor(color,cv2.COLOR_LAB2RGB)[0,0].tolist()
            rgb = sample_rgb('picked')
            previous = sample_rgb('base')
            self.quality_original[sample['parent']-1].update(rgb=previous,color='#%02x%02x%02x'%tuple(previous))
            self.quality_original.append(dict(id=ident,rgb=rgb,color='#%02x%02x%02x'%tuple(rgb),
                                              label=f'Образец {ident}',enabled=True))
            total = int(self.raster.valid.sum())
            for row in self.quality_original:
                count = int(counts[row['id']])
                row.update(pixels=count,share=round(100*count/total,1))
            self.palette_labels = labels
            self.palette_samples.append(sample)
            self.quality_groups.append(ident)
            self.update_palette()
            self.focusPalette(ident)
            self.notify(paletteMessage=f'Создана группа {ident}. На карте подсвечены совпадения.')
            return ident
        except ValueError as exc:
            self.notify(paletteMessage=str(exc))
            return 0

    @Slot()
    def resetPalette(self):
        if not self.data['busy'] and self.data['qualityReady']:
            self.quality_original = [dict(row) for row in self.base_palette]
            self.palette_samples = []
            self.palette_labels = np.load(Path(self.temp.name)/'palette-labels.npy',allow_pickle=False)
            self.notify(paletteMessage='')
            self.quality_groups=list(range(1,len(self.quality_original)+1))
            self.update_palette()
            self.focusPalette(0)

    @Slot(int)
    def focusPalette(self, ident):
        if self.palette_labels is None:
            return
        selected=self.raster.valid & (self.palette_labels==ident) if ident else np.zeros(self.raster.valid.shape,bool)
        rgba=np.zeros((*selected.shape,4),np.uint8);rgba[:,:,:3]=[236,48,125];rgba[:,:,3]=selected.astype(np.uint8)*150
        Image.fromarray(rgba).save(Path(self.temp.name)/'palette-focus.png')
        self.revision+=1
        self.notify(paletteUrl=self.url('palette-focus.png'))

    @Slot()
    def discardAuto(self):
        if not self.data['busy']:
            self.clear_auto_preview()
            self.reset_review()
            self.notify(status='Предпросмотр закрыт. Полигоны не изменены.', error=False)

    @Slot(int, int, str)
    def previewAuto(self, classes, minimum, profile):
        if self.raster is None or self.data['busy']:
            return
        self.clear_auto_preview()
        self.reset_review()
        self.auto_revision = self.edit_revision
        self.start('auto_preview', auto_job, self.raster, self.result, classes, minimum, profile, self.temp.name)

    @Slot()
    def nextReviewSite(self):
        self.visit_review(self.review_index+1)

    @Slot()
    def previousReviewSite(self):
        self.visit_review(self.review_index-1)

    def visit_review(self,index):
        if (self.data['busy'] or self.data['geometryReady'] or self.edit_points
                or not 0 <= index < len(self.review_sites)):
            return
        self.finish_review('Просмотрен')
        if self.auto_preview is None:
            self.reset_editor()
            self.selected = -1
            self.refresh_selection()
        self.review_index = index
        self.review_started = time.monotonic()
        self.show_review()
        self.focusPolygon.emit(*self.review_sites[index]['bounds'])

    def show_review(self):
        site=self.review_sites[self.review_index]
        self.notify(reviewCount=len(self.review_sites),reviewPosition=self.review_index+1,
                    reviewText=site['reason'],reviewTool=site['tool'],reviewMarker=site['marker'],
                    reviewDecision=site.get('decision',''),reviewActive=self.review_started is not None)

    def finish_review(self, action):
        if self.review_started is None or self.review_index < 0:
            return
        site = self.review_sites[self.review_index]
        seconds = round(time.monotonic()-self.review_started, 1)
        self.review_log.append((self.review_index+1, *site['bounds'], site['reason'], action, seconds))
        self.review_started = None
        if action!='Просмотрен' or not site.get('decision'):
            site['decision']=action
        self.notify(reviewLogged=len(self.review_log),reviewDecision=site['decision'],reviewActive=False)

    @Slot()
    def confirmReviewSite(self):
        if self.data['busy'] or self.review_started is None:
            return
        self.finish_review('Принят без правки')
        self.nextReviewSite()

    @Slot(QUrl)
    def exportReviewLog(self, url):
        path = Path(url.toLocalFile())
        if not self.review_log or not path.name:
            return
        if path.suffix.lower() != '.csv':
            path = Path(str(path)+'.csv')
        try:
            with path.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, delimiter=';')
                writer.writerow(('Участок','X1','Y1','X2','Y2','Причина','Действие','Секунд на участке'))
                writer.writerows(self.review_log)
        except OSError as exc:
            self.notify(status=str(exc),error=True)
            return
        self.notify(status='Журнал проверки сохранён.',error=False)

    @Slot(result=bool)
    def applyForReview(self):
        if self.review_index < 0 or self.auto_preview is None or self.data['busy']:
            return False
        bounds = self.review_sites[self.review_index]['bounds']
        previous = self.auto_preview
        self.acceptAuto()
        if self.result is not previous:
            return False
        self.focusPolygon.emit(*bounds)
        return True

    @Slot()
    def acceptAuto(self):
        if self.auto_preview is None or self.data['busy']:
            return
        if self.auto_revision != self.edit_revision:
            self.clear_auto_preview()
            self.notify(status='Данные изменились. Пересчитайте выделение.', error=True)
            return
        try:
            shutil.copyfile(Path(self.temp.name) / 'auto-preview.png', Path(self.temp.name) / 'overlay.png')
        except OSError as exc:
            self.notify(status=str(exc), error=True)
            return
        sites, cursor, started = self.review_sites, self.review_index, self.review_started
        output = self.auto_preview
        self.record_undo(('result', self.result))
        self.undo_stack = self.undo_stack[-12:]
        self.revision += 1
        self.set_result(output)
        self.review_sites, self.review_index, self.review_started = sites, cursor, started
        if cursor>=0:
            self.show_review()
        else:
            self.notify(reviewCount=len(sites))
        self.points, self.roi, self.proposal = [], None, None
        self.notify(hintUrl='', hintCount=0, proposalReady=False, touchesEdge=False,
                    status=f'Выделение применено: {len(output.features)} полигонов. Доступна отмена.', error=False)

    @Slot(QUrl)
    def exportFile(self, url):
        if self.result is not None:
            path = url.toLocalFile()
            if not path.lower().endswith('.gpkg'):
                path += '.gpkg'
            self.start('export', export_gpkg, self.result, path)

    @Slot()
    def poll(self):
        if getattr(self,'_closing',False):return
        self.poll_basemap()
        if self.data['qualityBusy'] and not self.data['qualityCancelPending']:
            try:
                progress=json.loads((Path(self.temp.name)/'quality-progress.json').read_text())
                value = max(self.data['qualityProgress'],progress['progress'])
                start = 45 if self.kind == 'quality_segment' else 0
                fraction = max(0,(value-start)/(100-start))
                elapsed = time.monotonic()-self.quality_started
                eta = 'Оцениваю время…'
                if fraction >= .04 and elapsed >= 1.:
                    seconds = max(1,round(elapsed*(1-fraction)/fraction))
                    eta = (f'Осталось примерно {seconds} с' if seconds<60
                           else f'Осталось примерно {round(seconds/60) or 1} мин')
                if value >= 96:
                    eta = 'Завершение расчёта…'
                self.notify(qualityProgress=value,qualityStage=progress['stage'],qualityETA=eta)
            except (OSError, ValueError, KeyError):
                pass
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        prepare_next = False
        try:
            output = future.result()
            if self.data['qualityBusy'] and (Path(self.temp.name)/'quality-cancel').exists():
                raise Cancelled('Расчёт остановлен. Прежние полигоны сохранены.')
            self.revision += 1
            if self.kind in ('load', 'project_load'):
                self.review_log.clear()
                self.notify(reviewLogged=0)
                self.reset_quality()
                self.clear_auto_preview()
                self.remove_active_recovery()
                project = output if self.kind == 'project_load' else None
                self.raster = project.raster if project else output
                self.edge_index = None
                self.result, self.tree = None, None
                self.table.set_result(None)
                self.selected = -1
                self.reset_editor()
                self.undo_stack.clear()
                self.redo_stack.clear()
                self.notify(canRedo=False)
                self.points = project.points if project else []
                self.roi = project.roi if project else None
                self.proposal = None
                self.pending_areas = []
                self.reset_basemap()
                self.view = dict(project.view) if project else dict(VIEW_DEFAULTS)
                self.view_dirty = False
                self.notify(layerSettings=dict(self.view))
                self.refresh_area()
                raster = self.raster
                h, w = raster.valid.shape
                sw, sh = raster.source_size
                self.project_path = (project.source_project if self.is_recovery else self.load_target) if project else ''
                self.restored_path = Path(self.load_target) if project and self.is_recovery else None
                self.notify(filename=Path(raster.path).name, width=w, height=h,
                            metadata=f'{raster.crs_label}  ·  исходник {sw} × {sh} px  ·  обработка {w} × {h} px',
                            rasterUrl=self.url('raster.png'), overlayUrl='', selectionUrl='', selection={},
                            count=0, colors=[], fields=[], hintUrl='', hintCount=0, proposalReady=False,
                            touchesEdge=False, canUndo=False, projectPath=self.project_path,
                            projectName=Path(self.project_path).name if self.project_path else '', autosaveStatus='')
                if project and project.result is not None:
                    self.set_result(project.result)
                self.draw_hints()
                self.edit_revision += 1
                unsaved = project is None or self.is_recovery
                self.autosaved_revision = self.edit_revision - 1 if unsaved else self.edit_revision
                self.notify(attributeEpoch=self.edit_revision, dirty=unsaved, autosaveStatus='Ожидает автосохранения' if unsaved else 'Проект сохранён', status=('Восстановлена рабочая копия. Сохраните проект.' if project and self.is_recovery
                            else 'Проект открыт.' if project else 'Карта загружена. Можно выделить области.') +
                            (' Подсказки восстановлены; выделение нужно пересчитать.' if self.points else ''))
                self.mapLoaded.emit()
                prepare_next = self.kind=='load' and self.prepare_after_load
                self.prepare_after_load=False
            elif self.kind == 'area_load':
                self.pending_areas = output
                self.notify(areaOptions=[name for name, _ in output], status='Выберите область обработки.')
                QTimer.singleShot(0, self, self.areaPrepared.emit)
            elif self.kind == 'area_apply':
                self.record_undo(('area', self.area_snapshot))
                self.undo_stack = self.undo_stack[-12:]
                self.raster, result = output
                self.pending_areas = []
                self.clear_auto_preview()
                self.reset_quality()
                self.set_result(result)
                self.points, self.roi, self.proposal = [], None, None
                self.refresh_area()
                self.notify(rasterUrl=self.url('raster.png'), hintUrl='', hintCount=0, proposalReady=False,
                            touchesEdge=False, status='Карта и полигоны обрезаны по KML. Доступна отмена.' if self.raster.area is not None
                            else 'Карта восстановлена целиком. Полигоны сохранены в текущем виде.')
            elif self.kind in ('segment', 'apply'):
                self.record_undo(('result', self.result))
                self.undo_stack = self.undo_stack[-12:]
                self.set_result(output)
                self.points, self.roi, self.proposal = [], None, None
                self.notify(hintUrl='', hintCount=0, proposalReady=False, touchesEdge=False,
                            status=f'{len(output.features)} полигонов. Проверьте границы перед экспортом.')
            elif self.kind == 'quality_prepare':
                self.base_palette=output['palette']
                self.quality_original=[dict(row) for row in self.base_palette]
                self.quality_groups=list(range(1,len(self.quality_original)+1))
                self.palette_labels=np.load(Path(self.temp.name)/'palette-labels.npy',allow_pickle=False)
                self.update_palette()
                self.notify(qualityReady=True, qualityNote=f"{output['source_note']} · {output['width']} × {output['height']} px",
                            qualityEvidenceUrl=self.url('quality-evidence.png'), status='Карта подготовлена. Можно сразу создавать полигоны или уточнить палитру.')
                QTimer.singleShot(0,self,self.palettePrepared.emit)
            elif self.kind in ('auto_preview', 'quality_segment'):
                if self.kind=='quality_segment':
                    output,info=output
                    self.review_sites = info['sites']
                    self.review_index = -1
                    self.notify(reviewCount=len(self.review_sites))
                    self.notify(qualityInfo=f"Близкие цвета: {info['uncertain_share']}% области анализа. Это индикатор неоднозначности, не оценка точности.")
                if self.auto_revision != self.edit_revision:
                    self.clear_auto_preview()
                    raise ValueError('Данные изменились. Пересчитайте выделение.')
                self.auto_preview = output
                protected = sum(protected_feature(f, self.result.fields) for f in self.result.features) if self.result else 0
                self.notify(autoReady=True, autoPreviewUrl=self.url('auto-preview.png'),
                            autoSummary=f'Всего: {len(output.features)} · сохранено: {protected} · новых: {len(output.features) - protected}',
                            status='Предпросмотр автоматического выделения. Примените результат или закройте его.')
            elif self.kind in ('drag', 'merge_direct'):
                self.edit_preview = output
                self.commit_geometry()
            elif self.kind == 'geometry':
                self.edit_preview = output
                self.notify(editPreviewUrl=self.url('edit-preview.png'), editSelectionUrl=self.url('edit-selection.png'), geometryReady=True,
                            status='Предпросмотр правки. Проверьте границу и нажмите «Применить правку».')
            elif self.kind == 'prompt':
                self.proposal = output
                self.draw_hints()
                self.notify(proposalReady=True, touchesEdge=output.touches_edge,
                            status='Предварительное выделение. Уточните подсказки или примите область.' +
                            (' Внимание: выделение достигает рамки анализа.' if output.touches_edge else ''))
            elif self.kind == 'project_save':
                self.view_dirty = False
                self.project_path = self.save_target
                self.autosaved_revision = self.edit_revision
                self.remove_active_recovery(include_restored=True)
                self.notify(dirty=False, projectPath=self.project_path, projectName=Path(self.project_path).name,
                            autosaveStatus='Проект сохранён', status=f'Проект сохранён: {output}')
                # Emit after busy has cleared, so Save-and-continue can safely open or close.
                QTimer.singleShot(0,self,self.projectSaved.emit)
            elif self.kind == 'autosave':
                self.view_dirty = False
                self.autosaved_revision = self.edit_revision
                self.notify(autosaveStatus='Копия восстановления · ' + datetime.now().strftime('%H:%M'),
                            status='Копия восстановления обновлена. Проект можно сохранить через Ctrl/Cmd+S.')
            else:
                self.notify(status=f'GeoPackage экспортирован: {output}')
        except Cancelled as exc:
            self.notify(status=str(exc),error=False)
        except Exception as exc:
            self.notify(status=str(exc), error=True)
        finally:
            self.notify(busy=False,qualityBusy=False,qualityCancelPending=False)
        if prepare_next:
            self.prepareQuality()

    def set_result(self, output):
        self.edge_index = None
        self.reset_editor()
        self.result = output
        self.table.set_result(output)
        self.pending_bulk = None
        self.tree = STRtree([f['geometry'] for f in output.features]) if output is not None else None
        self.selected = -1
        colors = []
        if output is not None:
            for i, rgb in enumerate(output.colors):
                colors.append({'label': f'Группа {i + 1}', 'color': '#%02x%02x%02x' % tuple(rgb),
                               'count': sum(f['properties']['class_id'] == i+1 for f in output.features)})
        self.mark_dirty()
        self.notify(overlayUrl=self.url('overlay.png') if output is not None else '', selectionUrl='',
                    selection={}, dirty=True, count=len(output.features) if output else 0, colors=colors,
                    canUndo=bool(self.undo_stack), fields=output.fields if output else [])

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
        self.mark_dirty()
        self.proposal = None
        self.draw_hints()
        self.notify(proposalReady=False, touchesEdge=False, error=False,
                    status='Подсказка добавлена. Нажмите «Уточнить область».')

    @Slot()
    def removeHint(self):
        if not self.points or self.data['busy']:
            return
        self.points.pop()
        self.mark_dirty()
        self.proposal = None
        if not self.points:
            self.roi = None
        self.draw_hints()
        self.notify(proposalReady=False, touchesEdge=False)

    @Slot()
    def clearHints(self):
        if self.data['busy']:
            return
        self.clear_hints()

    def clear_hints(self):
        if self.points:
            self.mark_dirty()
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
        matches = [i for i in matches if self.feature_visible(self.result.features[int(i)])]
        self.reset_editor()
        self.selected = int(matches[0]) if len(matches) else -1
        self.refresh_selection()

    def feature_visible(self, feature):
        return True

    def refresh_selection(self):
        if self.selected < 0:
            self.notify(selection={}, selectionUrl='')
            return
        feature = self.result.features[self.selected]
        inverse = ~self.raster.transform
        image = Image.new('RGBA', (self.data['width'], self.data['height']))
        draw = ImageDraw.Draw(image)
        indices = self.edit_selected or [self.selected]
        for number, index in enumerate(indices):
            geometry = self.result.features[index]['geometry']
            for ring in [geometry.exterior, *geometry.interiors]:
                points = [inverse * point for point in ring.coords]
                draw.line(points, fill=(255, 255, 255, 255), width=6)
                draw.line(points, fill=(0,102,255,255) if number == 0 else (235,126,30,255), width=3)
        image.save(Path(self.temp.name) / 'selection.png')
        self.revision += 1
        self.notify(selection=dict(feature['properties']), selectionUrl=self.url('selection.png'),
                    editSelected=[dict(self.result.features[i]['properties']) for i in self.edit_selected])

    def invalidate_edit_preview(self):
        self.edit_preview = None
        self.notify(editPreviewUrl='', editSelectionUrl='', geometryReady=False)

    def reset_editor(self):
        self.edge_drag = None
        self.edit_selected, self.edit_points = [], []
        self.edit_preview = None
        self.vertex_source = None
        self.notify(editSelected=[], editPoints=[], vertexPoints=[], vertexSource={}, editPreviewUrl='', editSelectionUrl='', geometryReady=False)

    def refresh_vertices(self):
        points = []
        if self.edit_tool == 4 and len(self.edit_selected) == 1:
            geometry = transform_geometry(self.result.features[self.edit_selected[0]]['geometry'], ~self.raster.transform)
            points = [dict(x=float(x),y=float(y)) for ring in [geometry.exterior,*geometry.interiors]
                      for x,y in list(ring.coords)[:-1]]
        self.notify(vertexPoints=points)

    @Slot(int)
    def setEditTool(self, tool):
        if self.data['busy']:
            return
        self.edge_drag = None
        self.edit_tool = tool
        self.vertex_source = None
        self.notify(vertexSource={})
        self.clearEditLine()
        if tool == 3:
            self.clearEditSelection()
        elif tool in (1, 2, 4):
            limit = 2 if tool == 2 else 1
            if len(self.edit_selected) > limit:
                self.set_edit_selection(self.edit_selected[:limit])
        self.refresh_vertices()

    def direct_index(self):
        if self.result is None or self.data['busy'] or self.edit_tool!=5:
            return None
        if self.edge_index is None:
            self.edge_index = EdgeIndex(self.result)
        return self.edge_index

    @Slot(float,float,float,result='QVariantMap')
    def edgeHover(self,x,y,tolerance):
        index = self.direct_index()
        hit = index.hit(x,y,tolerance) if index else None
        return dict(x=hit[2].x,y=hit[2].y) if hit else {}

    @Slot(float,float,float,bool,result='QVariantList')
    def beginEdgeDrag(self,x,y,tolerance,single):
        self.edge_drag = None
        index = self.direct_index()
        if index:
            try:
                self.edge_drag = index.plan(x,y,tolerance,single,self.tree)
            except ValueError as exc:
                self.notify(status=str(exc),error=True)
        if self.edge_drag is None:
            return []
        return [dict(x=float(pt[0]),y=float(pt[1]),weight=float(w))
                for pt,w in zip(self.edge_drag.pixels,self.edge_drag.weights)]

    @Slot()
    def cancelEdgeDrag(self):
        self.edge_drag = None

    @Slot(float,float)
    def finishEdgeDrag(self,dx,dy):
        drag, self.edge_drag = self.edge_drag, None
        if drag is None or self.data['busy'] or self.edit_tool!=5:
            return
        self.geometry_primary_id = None
        self.geometry_operation = 'boundary'
        self.start('drag',drag_job,self.result,drag,dx,dy,self.temp.name)

    @Slot(float, float, float, result=bool)
    def beginVertexDrag(self, x, y, tolerance):
        if self.data['busy'] or self.data['geometryReady'] or len(self.edit_selected) != 1 or not self.data['vertexPoints']:
            return False
        points = self.data['vertexPoints']
        nearest = min(points, key=lambda p:(p['x']-x)**2+(p['y']-y)**2)
        if (nearest['x']-x)**2+(nearest['y']-y)**2 > tolerance**2:
            return False
        self.vertex_source = (nearest['x'],nearest['y'])
        self.invalidate_edit_preview()
        self.notify(vertexSource=nearest,error=False)
        return True

    @Slot(float, float)
    def finishVertexDrag(self, x, y):
        if self.vertex_source is None or self.data['busy']:
            return
        self.edit_points = [self.vertex_source,(x,y)]
        self.vertex_source = None
        self.notify(vertexSource={},editPoints=[dict(x=p[0],y=p[1]) for p in self.edit_points])
        self.previewGeometry('vertex')

    @Slot()
    def cancelVertexDrag(self):
        self.vertex_source = None
        self.notify(vertexSource={})

    @Slot()
    def clearEditSelection(self):
        if self.data['busy']:
            return
        self.reset_editor()
        self.selected = -1
        self.refresh_selection()

    @Slot(float, float)
    def selectForEdit(self, x, y):
        if self.tree is None or self.data['busy']:
            return
        matches = self.tree.query(Point(self.raster.transform * (x,y)), predicate='intersects')
        matches = [i for i in matches if self.feature_visible(self.result.features[int(i)])]
        if not len(matches):
            return
        index = int(matches[0])
        indices = list(self.edit_selected)
        if index in indices:
            indices.remove(index)
        elif self.edit_tool == 0 or len(indices) < (1 if self.edit_tool in (1, 4) else 2):
            indices.append(index)
        else:
            self.notify(status='Снимите выбор с другого полигона повторным щелчком.', error=True)
            return
        self.set_edit_selection(indices)

    def set_edit_selection(self, indices):
        self.edit_selected = list(indices)
        self.edit_points = []
        self.invalidate_edit_preview()
        self.selected = self.edit_selected[0] if self.edit_selected else -1
        self.notify(editSelected=[dict(self.result.features[i]['properties']) for i in self.edit_selected],
                    editPoints=[], error=False)
        self.refresh_selection()
        self.refresh_vertices()

    @Slot(str, bool)
    def selectLasso(self, coordinates, additive):
        if self.result is None or self.tree is None or self.data['busy'] or self.edit_tool != 0:
            return
        try:
            if len(coordinates) > 250000:
                raise ValueError('Слишком длинный контур лассо.')
            indices = lasso_indices(self.result, self.tree, json.loads(coordinates))
            indices = [i for i in indices if self.feature_visible(self.result.features[i])]
            if additive:
                indices = list(self.edit_selected) + [i for i in indices if i not in self.edit_selected]
            self.set_edit_selection(indices)
            self.notify(status=f'Выбрано полигонов: {len(indices)}. Щелчок снимает или добавляет выбор.')
        except (ValueError, TypeError, OverflowError) as exc:
            self.notify(status=str(exc), error=True)

    @Slot()
    def mergeSelected(self):
        if self.result is None or self.data['busy'] or len(self.edit_selected) < 2:
            return
        ids = [self.result.features[i]['properties']['object_id'] for i in self.edit_selected]
        self.invalidate_edit_preview()
        self.geometry_operation, self.geometry_primary_id = 'merge', ids[0]
        self.start('merge_direct', edit_job, self.result, ids, 'merge', [], self.temp.name, self.raster)

    @Slot(float, float)
    def addEditPoint(self, x, y):
        if self.raster is None or self.data['busy'] or (self.edit_tool != 3 and not self.edit_selected):
            return
        if not np.isfinite([x,y]).all() or len(self.edit_points) >= 256:
            return
        if self.edit_points and np.linalg.norm(np.array(self.edit_points[-1]) - [x,y]) < 0.01:
            return
        self.edit_points.append((x,y))
        self.invalidate_edit_preview()
        self.notify(editPoints=[dict(x=p[0], y=p[1]) for p in self.edit_points], error=False)

    @Slot()
    def removeEditPoint(self):
        if self.data['busy'] or not self.edit_points:
            return
        self.edit_points.pop()
        self.invalidate_edit_preview()
        self.notify(editPoints=[dict(x=p[0], y=p[1]) for p in self.edit_points])

    @Slot()
    def clearEditLine(self):
        if self.data['busy']:
            return
        self.edit_points = []
        self.vertex_source = None
        self.invalidate_edit_preview()
        self.notify(editPoints=[],vertexSource={})

    @Slot(str)
    def previewGeometry(self, operation):
        if self.raster is None or (self.result is None and operation != 'draw') or self.data['busy']:
            return
        self.invalidate_edit_preview()
        ids = [self.result.features[i]['properties']['object_id'] for i in self.edit_selected]
        self.geometry_operation = operation
        self.geometry_primary_id = ids[0] if ids else None
        self.start('geometry', edit_job, self.result, ids, operation, list(self.edit_points), self.temp.name, self.raster, self.data.get('drawClassId'))

    @Slot()
    def acceptGeometry(self):
        if self.edit_preview is None or self.data['busy']:
            return
        self.commit_geometry()

    def commit_geometry(self):
        try:
            shutil.copyfile(Path(self.temp.name) / 'edit-preview.png', Path(self.temp.name) / 'overlay.png')
        except OSError as exc:
            if self.kind=='drag':
                self.edit_preview = None
            self.notify(status=str(exc), error=True)
            return
        actions = dict(merge='Объединение',split='Разделение',boundary='Общая граница',
                       draw='Новый полигон',vertex='Перемещение вершины')
        self.finish_review(actions.get(self.geometry_operation,self.geometry_operation))
        output = self.edit_preview
        self.record_undo(('result', self.result))
        self.undo_stack = self.undo_stack[-12:]
        self.revision += 1
        self.set_result(output)
        target_id = (max(f['properties']['object_id'] for f in output.features) if self.geometry_operation == 'draw'
                     else self.geometry_primary_id if self.geometry_operation == 'vertex' else None)
        if target_id is not None:
            self.selected = next(i for i,f in enumerate(output.features) if f['properties']['object_id']==target_id)
            if self.geometry_operation == 'vertex':
                self.edit_selected = [self.selected]
            self.refresh_selection()
            self.refresh_vertices()
        # A segmentation proposal computed before this geometry edit must not be accepted later.
        self.clear_hints()
        self.notify(status='Ручная правка применена. Можно отменить её или сохранить проект.', error=False)

    @Slot(str, str, result=bool)
    def saveField(self, definition, original_key):
        if self.raster is None or self.data['busy']:
            return False
        try:
            field = json.loads(definition)
            old = self.result
            base = old or Result(self.raster, np.zeros(self.raster.valid.shape,dtype='uint16'), [], [])
            fields = list(base.fields)
            if original_key:
                index = next(i for i,f in enumerate(fields) if f['key']==original_key)
                if field['key'] != original_key:
                    raise ValueError('Код существующего поля менять нельзя; измените его название.')
                fields[index] = field
            else:
                fields.append(field)
            output = change_schema(base,fields)
            if old is None:
                save_overlay(output,Path(self.temp.name)/'overlay.png')
            self.record_undo(('result',old));self.undo_stack=self.undo_stack[-12:]
            self.revision += 1
            self.set_result(output)
            self.notify(schemaError='',status='Структура атрибутов обновлена. Изменение можно отменить.',error=False)
            return True
        except (ValueError, KeyError, StopIteration, TypeError) as exc:
            self.notify(schemaError=str(exc),status=str(exc),error=True)
            return False

    @Slot(str)
    def removeField(self, key):
        if self.result is None or self.data['busy']:
            return
        if not any(f['key']==key for f in self.result.fields):
            return
        output=change_schema(self.result,[f for f in self.result.fields if f['key']!=key])
        self.record_undo(('result',self.result));self.undo_stack=self.undo_stack[-12:]
        self.revision+=1
        self.set_result(output)
        self.notify(status='Поле и его значения удалены. Можно отменить последнее действие.',error=False)

    def table_stats(self):
        self.notify(tableColumns=self.table.columns,tableMatched=len(self.table.rows),
                    tableTotal=len(self.result.features) if self.result else 0,
                    tableSelected=len(self.table.selected_ids),tableSort=self.table.sort_key,
                    tableDescending=self.table.descending,bulkFields=editable_fields(self.result))

    @Slot(str, str, bool)
    @Slot(str, str, bool, int)
    def filterTable(self,query,review,missing_only,class_id=0):
        self.pending_bulk = None
        self.table.filter(query,review,missing_only,class_id)

    @Slot(str)
    def sortTable(self,key):
        self.table.sort_by(key)

    @Slot(int)
    def toggleTableObject(self,oid):
        if not self.data['busy']:
            self.table.choose(oid)
            self.pending_bulk = None

    @Slot(bool)
    def chooseFiltered(self,checked):
        if not self.data['busy']:
            self.table.choose_filtered(checked)
            self.pending_bulk = None

    @Slot(int)
    def focusTableObject(self,oid):
        if self.result is None or self.data['busy']:
            return
        index=next((i for i,f in enumerate(self.result.features) if f['properties']['object_id']==oid),None)
        if index is None:
            return
        self.reset_editor();self.selected=index;self.refresh_selection()
        geometry=transform_geometry(self.result.features[index]['geometry'],~self.raster.transform)
        self.focusPolygon.emit(*geometry.bounds)

    @Slot(str, str, bool, result=bool)
    def prepareBulk(self,key,value_json,only_empty):
        if self.result is None or self.data['busy']:
            return False
        self.pending_bulk = None
        try:
            field,value,changes=plan_bulk(self.result,self.table.selected_ids,key,json.loads(value_json),only_empty)
            if not changes:
                raise ValueError('Нет изменений: значения уже совпадают либо заполненные поля пропущены.')
            self.pending_bulk=(self.edit_revision,changes)
            shown='Очистить значение' if value is None or value=='' else display(value)
            shown=shown[:160]+('…' if len(shown)>160 else '')
            self.notify(bulkSummary=f"Поле: {field['label']}\nЗначение: {shown}\nОтмечено: {len(self.table.selected_ids)} · Изменится: {len(changes)}\n"
                        'Изменения применятся только к отмеченным объектам и будут отменяться одним действием.',error=False)
            return True
        except (ValueError,TypeError) as exc:
            self.notify(status=str(exc),error=True)
            return False

    @Slot()
    def cancelBulk(self):
        self.pending_bulk = None

    @Slot()
    def applyBulk(self):
        if self.pending_bulk is None or self.data['busy']:
            return
        revision,changes=self.pending_bulk;self.pending_bulk=None
        if revision!=self.edit_revision:
            self.notify(status='Данные изменились после предпросмотра. Подготовьте заполнение заново.',error=True)
            return
        old={oid:pair[0] for oid,pair in changes.items()}
        self.record_undo(('bulk',old));self.undo_stack=self.undo_stack[-12:]
        self.invalidate_edit_preview()
        for feature in self.result.features:
            oid=feature['properties']['object_id']
            if oid in changes:feature['properties']=changes[oid][1]
        self.mark_dirty();self.table.refresh();self.refresh_selection()
        self.notify(canUndo=True,status=f'Атрибуты изменены у {len(changes)} полигонов. Можно отменить одним действием.',error=False)

    def attribute_indices(self, scope):
        if self.result is None:
            return []
        mode = scope.get('mode', 'selected')
        if mode == 'selected':
            return list(self.edit_selected) or ([self.selected] if self.selected >= 0 else [])
        if mode == 'group':
            group = int(scope.get('group', 0))
            return [i for i,f in enumerate(self.result.features) if group>0 and f['properties']['class_id']==group]
        if mode == 'filtered':
            return self.table.matching_indices(str(scope.get('query','')),scope.get('review','all'),
                                              bool(scope.get('missing',False)),int(scope.get('group',0)))
        raise ValueError('Неизвестная область заполнения атрибутов.')

    @Slot(str, result='QVariantMap')
    def attributeScope(self, encoded):
        try:
            indices = self.attribute_indices(json.loads(encoded))
            values, mixed = {}, []
            for field in editable_fields(self.result):
                key = field['key']
                first = self.result.features[indices[0]]['properties'].get(key) if indices else None
                if any(self.result.features[i]['properties'].get(key)!=first for i in indices):
                    mixed.append(key)
                    values[key] = None
                else:
                    values[key] = first
            return dict(count=len(indices),values=values,mixed=mixed,epoch=self.edit_revision)
        except (ValueError,TypeError,AttributeError):
            return dict(count=0,values={},mixed=[],epoch=self.edit_revision)

    @Slot(str, str, bool, int, result=bool)
    def applyAttributeScope(self, encoded, patch_json, only_empty, epoch):
        if self.result is None or self.data['busy']:
            return False
        try:
            if epoch != self.edit_revision:
                raise ValueError('Данные изменились. Проверьте область заполнения заново.')
            indices = self.attribute_indices(json.loads(encoded))
            ids = {self.result.features[i]['properties']['object_id'] for i in indices}
            patch = json.loads(patch_json)
            if not isinstance(patch,dict) or not patch:
                raise ValueError('Измените хотя бы одно поле.')
            changes = {}
            # Validate every field before touching any feature. Different fields
            # may affect different objects when only-empty is enabled.
            for key,value in patch.items():
                _,_,planned = plan_bulk(self.result,ids,key,value,only_empty)
                for oid,(old,new) in planned.items():
                    if oid not in changes:
                        changes[oid] = (old,old.copy())
                    changes[oid][1].update({key:new[key], 'review':'edited'})
            if not changes:
                raise ValueError('Нет изменений: значения совпадают или заполненные поля пропущены.')
            self.pending_bulk = (self.edit_revision,changes)
            self.applyBulk()
            return True
        except (ValueError,TypeError,AttributeError) as exc:
            self.notify(status=str(exc),error=True)
            return False

    @Slot(str, str)
    def saveAttributes(self, note, custom_json):
        if self.selected < 0 or self.data['busy']:
            return
        try:
            values=values_for(self.result.fields,json.loads(custom_json))
        except (ValueError,TypeError,AttributeError) as exc:
            self.notify(status=str(exc),error=True)
            return
        self._update_attributes(dict(note=note,**values))

    @Slot(str, str, str, str)
    def updateAllAttributes(self, index, name, note, custom_json):
        if self.selected < 0 or self.data['busy']:
            return
        try:
            values=values_for(self.result.fields,json.loads(custom_json))
        except (ValueError,TypeError,AttributeError) as exc:
            self.notify(status=str(exc),error=True)
            return
        self._update_attributes(dict(dict(unit_index=index,unit_name=name,note=note),**values))

    @Slot(str, str, str)
    def updateAttributes(self, index, name, note):
        if self.selected < 0 or self.data['busy']:
            return
        self._update_attributes(dict(unit_index=index,unit_name=name,note=note))

    def _update_attributes(self,values):
        self.invalidate_edit_preview()
        props=self.result.features[self.selected]['properties']
        self.record_undo(('attribute',(self.selected,props.copy())))
        self.undo_stack=self.undo_stack[-12:]
        props.update(values,review='edited')
        self.mark_dirty();self.table.refresh();self.refresh_selection()
        self.notify(canUndo=True,status='Атрибуты применены. Сохраните проект.',error=False)

    def record_undo(self, entry):
        self.undo_stack.append(entry)
        self.redo_stack.clear()
        self.notify(canRedo=False)

    def history_snapshot(self):
        result = self.result
        if result is not None:
            # Geometry, labels and embedded rasters are immutable across edits;
            # only attribute dictionaries need their own history copy.
            result = Result(result.raster, result.labels,
                [dict(geometry=f['geometry'], properties=copy.deepcopy(f['properties'])) for f in result.features],
                copy.deepcopy(result.colors), copy.deepcopy(result.fields))
        selected_id = (result.features[self.selected]['properties']['object_id']
                       if result is not None and 0 <= self.selected < len(result.features) else None)
        return self.raster, result, list(self.points), self.roi, selected_id

    def restore_snapshot(self, snapshot):
        raster, result, points, roi, selected_id = snapshot
        changed_raster = raster is not self.raster
        self.raster, self.points, self.roi = raster, list(points), roi
        self.proposal = None
        self.revision += 1
        if changed_raster:
            self.reset_quality()
            self.reset_basemap()
            save_preview(raster, Path(self.temp.name)/'raster.png')
        if result is not None:
            save_overlay(result, Path(self.temp.name)/'overlay.png')
        self.set_result(result)
        if selected_id is not None and result is not None:
            self.selected = next((i for i,f in enumerate(result.features) if f['properties']['object_id']==selected_id), -1)
            self.refresh_selection()
        self.refresh_area()
        self.draw_hints()
        if changed_raster:
            self.notify(rasterUrl=self.url('raster.png'))

    @Slot()
    def redo(self):
        if self.data['busy'] or not self.redo_stack:
            return
        self.restoring_history = True
        try:
            self.reset_review()
            self.undo_stack.append(('snapshot', self.history_snapshot()))
            self.undo_stack = self.undo_stack[-12:]
            self.restore_snapshot(self.redo_stack.pop())
            self.notify(canUndo=True, canRedo=bool(self.redo_stack), error=False, status='Действие повторено.')
        finally:
            self.restoring_history = False

    @Slot()
    def undo(self):
        if not self.data['busy']:
            self.reset_review()
        if not self.undo_stack or self.data['busy']:
            return
        self.restoring_history = True
        try:
            self.redo_stack.append(self.history_snapshot())
            self.redo_stack = self.redo_stack[-12:]
            kind, previous = self.undo_stack.pop()
            if kind == 'snapshot':
                self.restore_snapshot(previous)
            elif kind == 'area':
                self.raster, result, self.points, self.roi = previous
                self.clear_auto_preview()
                self.reset_quality()
                self.revision += 1
                save_preview(self.raster, Path(self.temp.name)/'raster.png')
                if result is not None:
                    save_overlay(result, Path(self.temp.name)/'overlay.png')
                self.set_result(result)
                self.refresh_area()
                self.draw_hints()
                self.notify(rasterUrl=self.url('raster.png'))
            elif kind == 'bulk':
                self.reset_editor()
                for feature in self.result.features:
                    oid=feature['properties']['object_id']
                    if oid in previous: feature['properties']=previous[oid]
                self.table.refresh();self.refresh_selection()
            elif kind == 'attribute':
                self.reset_editor()
                self.selected, properties = previous
                self.result.features[self.selected]['properties'] = properties
                self.table.refresh()
                self.refresh_selection()
            else:
                if previous is not None:
                    save_overlay(previous, Path(self.temp.name) / 'overlay.png')
                self.revision += 1
                self.set_result(previous)
            self.mark_dirty()
            self.notify(canUndo=bool(self.undo_stack), canRedo=bool(self.redo_stack), status='Последнее изменение отменено.')
        finally:
            self.restoring_history = False


def main():
    from .launcher import main as launch
    return launch()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
