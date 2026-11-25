"""The area workspace extends the established editor instead of replacing it."""
import base64
from contextlib import contextmanager
import copy
from datetime import datetime
from io import BytesIO
import json
import math
from pathlib import Path
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from PySide6.QtCore import Property, QTimer, QUrl, Signal, Slot
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from shapely.geometry import Point, box, LineString

from .app import Backend
from .attributes import defaults, validate_schema, values_for
from .cartography import preview_overlay, layout_defaults, validate_layout, layout_svg, write_layout, map_image
from .core import Result, save_preview, save_overlay, segment
from .document import Document, load_document, save_document, _copy_result
from .linework import detect_lines, validate_type, draw_line, split_line, join_lines, drag_line, edit_vertex
from .quality import prepare_job, quality_job, Cancelled, progress_scope
from .region import read_kml, save_area_preview
from .spatial import DISPLAY_CRS, measurements, measurement_label, project_geometry
from .styles import validate_style
from .workspace_export import export_workspace
from .editing import transform_geometry


def seam_sites(document, result):
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    from rasterio.features import rasterize
    from .review import marker
    records = []
    raster = result.raster
    pixel_area = abs(raster.transform.determinant)
    inverse = ~raster.transform
    def add(geometry, reason):
        if geometry.is_empty or geometry.area < pixel_area*.2:
            return
        pixels = transform_geometry(geometry, inverse)
        b = pixels.bounds
        bounds = [max(0, b[0]-24), max(0, b[1]-24), min(raster.rgb.shape[1], b[2]+24), min(raster.rgb.shape[0], b[3]+24)]
        mask = rasterize([(geometry, 1)], out_shape=raster.valid.shape, transform=raster.transform, all_touched=True, dtype='uint8').astype(bool)
        if mask.any():
            records.append(dict(bounds=bounds, reason=reason, tool=5, marker=marker(mask, 0, 0)))
    for i, first in enumerate(document.sheets):
        if first.result is None:
            continue
        a = [(project_geometry(f['geometry'], first.raster.crs_wkt), first.groups.get(f['properties']['class_id'],0)) for f in first.result.features]
        for second in document.sheets[i+1:]:
            if second.result is None:
                continue
            overlap = first.footprint().intersection(second.footprint())
            if document.area is not None:
                overlap = overlap.intersection(document.area)
            if overlap.is_empty:
                continue
            b = [(project_geometry(f['geometry'], second.raster.crs_wkt), second.groups.get(f['properties']['class_id'],0)) for f in second.result.features]
            tree = STRtree([g for g, _ in b])
            conflicts = []
            for geometry, cid in a:
                part = geometry.intersection(overlap)
                for j in tree.query(part, predicate='intersects'):
                    if cid != b[j][1]:
                        conflicts.append(part.intersection(b[j][0]))
            if conflicts:
                for geometry in sorted(__import__('geotrace.region', fromlist=['polygon_parts']).polygon_parts(unary_union(conflicts)), key=lambda g:-g.area)[:30]:
                    add(geometry, f'Разные классы в перекрытии: {first.name} / {second.name}. Проверьте соответствия и приоритет листов.')
    if document.area is not None:
        coverage = unary_union([f['geometry'] for f in result.features])
        for geometry in sorted(__import__('geotrace.region', fromlist=['polygon_parts']).polygon_parts(document.area.difference(coverage)), key=lambda g:-g.area)[:30]:
            add(geometry, 'Участок без полигонов: проверьте охват листов, исключённые группы и границы.')
    return records[:120]


def build_workspace(document, folder):
    canvas = document.canvas()
    result = document.compose(canvas)
    from .topology import clean_result_interiors
    result = clean_result_interiors(result)
    save_preview(canvas, Path(folder)/'raster.png')
    preview_overlay(document, result, Path(folder)/'overlay.png', Path(folder)/'lines.png')
    return document, canvas, result


def group_workspace(document, folder):
    """Group edits retain the current objects, including unreviewed geometry."""
    if document.manual is None:
        return build_workspace(document, folder)
    result = document.manual
    if len(document.sheets)>1:
        from dataclasses import replace
        raster=result.raster
        h,w=raster.valid.shape
        bounds=(*raster.transform*(0,h), *raster.transform*(w,0))
        rgb=np.array(map_image(document,bounds,w,h))[:,:,:3]
        result=Result(replace(raster,rgb=rgb),result.labels,result.features,result.colors,result.fields)
        document.manual=result
    save_preview(result.raster, Path(folder)/'raster.png')
    preview_overlay(document, result, Path(folder)/'overlay.png', Path(folder)/'lines.png')
    return document, result.raster, result


def import_sheets(document, paths, folder):
    document.add_paths(paths)
    return build_workspace(document, folder)


def open_workspace(path, folder):
    document = load_document(path, Path(folder)/'sources')
    if document.manual is not None and CRS.from_wkt(document.manual.raster.crs_wkt)==CRS.from_string(DISPLAY_CRS):
        # Reopening is not a request to recompute unreviewed objects. The saved
        # output is authoritative; future explicit digitisation uses the sheets.
        document.dissolve_classes=False
        return group_workspace(document,folder)
    return build_workspace(document, folder)


def prepare_sheet(raster, token, folder):
    save_preview(raster, Path(folder)/'palette-source.png')
    return prepare_job(raster, token, folder)


def recognise_sheet(document, token, groups, folder, samples, palette):
    sheet = document.sheet()
    previous = sheet.result
    if previous is not None:
        from .attributes import change_schema
        previous = change_schema(previous, document.fields)
    native, info = quality_job(sheet.raster, previous, token, groups, folder, samples, palette)
    sheet.recipe=dict(groups=groups,samples=samples,palette=palette)
    document.set_sheet_result(sheet.id, native)
    canvas = document.canvas()
    output = document.compose(canvas)
    preview_overlay(document, output, Path(folder)/'auto-preview.png')
    info['sites'] = project_review(info['sites'], sheet.raster, canvas)
    return document, canvas, output, info


def recognise_all(document, folder):
    """A bounded queue: one full sheet at a time, outside the KML mask."""
    checks=[]
    total=len(document.sheets)
    for index,sheet in enumerate(document.sheets):
        token = uuid.uuid4().hex
        prefix=f'{index+1}/{total} · {sheet.name} · '
        with progress_scope(100*index/total,.2/total,prefix):
            prepared = prepare_job(sheet.raster, token, folder)
        recipe=sheet.recipe
        groups=recipe.get('groups',list(range(1,len(prepared['palette'])+1)))
        with progress_scope(100*(index+.2)/total,.8/total,prefix):
            output, info = quality_job(sheet.raster, sheet.result, token, groups, folder,
                                       recipe.get('samples',()),recipe.get('palette',prepared['palette']))
        document.set_sheet_result(sheet.id, output)
        checks.append((sheet.raster,info['sites']))
    canvas=document.canvas();result=document.compose(canvas)
    preview_overlay(document,result,Path(folder)/'auto-preview.png')
    sites=[site for source,items in checks for site in project_review(items,source,canvas)]
    return document,canvas,result,dict(sites=sites[:240])


def detect_sheet_lines(raster,types,folder):
    from .quality import analysis_raster
    from .region import footprint
    from .document import line_parts
    fine,_=analysis_raster(raster)
    found=detect_lines(fine,types,folder)
    domain=project_geometry(footprint(raster),raster.crs_wkt,densify=True)
    return [dict(geometry=part,properties=dict(f['properties'])) for f in found for part in line_parts(f['geometry'].intersection(domain))]


def project_review(sites, source, canvas):
    from affine import Affine
    output = []
    inverse = ~canvas.transform
    def bounds(values):
        g = box(*values)
        g = transform_geometry(g, source.transform)
        g = project_geometry(g, source.crs_wkt)
        return transform_geometry(g, inverse).bounds
    for site in sites:
        item = copy.deepcopy(site)
        item['bounds'] = list(bounds(site['bounds']))
        marker = site.get('marker')
        if marker and marker.get('url', '').startswith('data:image/png;base64,'):
            x0, y0, x1, y1 = bounds((marker['x'], marker['y'], marker['x']+marker['w'], marker['y']+marker['h']))
            x0, y0 = math.floor(x0), math.floor(y0)
            w, h = max(1, math.ceil(x1)-x0), max(1, math.ceil(y1)-y0)
            rgba = np.array(Image.open(BytesIO(base64.b64decode(marker['url'].split(',', 1)[1]))).convert('RGBA'))
            pixels = np.zeros((h, w, 4), np.uint8)
            for channel in range(4):
                reproject(rgba[:, :, channel], pixels[:, :, channel],
                          src_transform=source.transform*Affine.translation(marker['x'], marker['y']), src_crs=source.crs_wkt,
                          dst_transform=canvas.transform*Affine.translation(x0, y0), dst_crs=DISPLAY_CRS,
                          resampling=Resampling.nearest, num_threads=1)
            buffer = BytesIO()
            Image.fromarray(pixels).save(buffer, format='PNG')
            item['marker'] = dict(x=x0, y=y0, w=w, h=h, url='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode())
        output.append(item)
    return output


def preview_smoothing(document,result,mode,strength,identities,folder):
    from .smoothing import smooth_result
    def check():
        if (Path(folder)/'smooth-cancel').exists():raise Cancelled('Предпросмотр сглаживания отменён.')
    output,info=smooth_result(document,result,mode,strength,identities,check)
    preview_overlay(document,output,Path(folder)/'edit-preview.png')
    return output,info


_render_application = None


def render_application():
    # A process-pool worker reuses its Qt application across previews and exports.
    global _render_application
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    _render_application = QGuiApplication.instance() or _render_application or QGuiApplication([])
    return _render_application


def viewport_images(document, result, bounds, width, height,labels_only=False):
    def encode(image):
        stream = BytesIO()
        image.save(stream, format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
    output = {} if labels_only else dict(detailUrl=encode(map_image(document, bounds, width, height, detailed=True)))
    labels=[];occupied=[];visible=box(*bounds)
    kx,ky=width/(bounds[2]-bounds[0]),height/(bounds[3]-bounds[1])
    candidates=[]
    for f in result.features if result is not None else []:
        if f['properties']['class_id'] in document.settings.get('hidden_classes',[]):continue
        cls=document.classes[f['properties']['class_id']-1];key=cls['style'].get('label','')
        if key and f['geometry'].intersects(visible):
            value=cls['name'] if key=='class_name' else f['properties'].get(key)
            if value is not None:candidates.append((f['geometry'].area,f['geometry'].intersection(visible).representative_point(),str(value),cls['style']['outline']))
    types={t['id']:t for t in document.line_types}
    if document.settings.get('lineVisible',True):
        for f in document.clipped_lines():
            t=types[f['properties']['type_id']];key=t['style'].get('label','')
            if key and f['geometry'].intersects(visible):
                value=t['name'] if key=='type_name' else f['properties'].get(key)
                if value is not None:candidates.append((f['geometry'].length,f['geometry'].interpolate(.5,normalized=True),str(value),t['style']['color']))
    for _,point,value,color in sorted(candidates,key=lambda r:-r[0]):
        if point.is_empty:continue
        value=value[:100];x=(point.x-bounds[0])*kx;y=(bounds[3]-point.y)*ky
        rect=box(x-3,y-15,x+max(12,len(value)*7)+3,y+3)
        if any(rect.intersects(r) for r in occupied):continue
        occupied.append(rect);labels.append(dict(x=point.x,y=point.y,text=value,color=color))
        if len(labels)>=300:break
    output['detailLabels']=labels
    return output


def layout_preview(document, result, settings, folder):
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    application = render_application()
    pages = layout_svg(document, result, settings, preview=True)
    renderer = QSvgRenderer(QByteArray(pages[0].encode()))
    if not renderer.isValid():
        raise ValueError('Не удалось построить макет.')
    size = renderer.defaultSize()
    image = QImage(size, QImage.Format_ARGB32_Premultiplied)
    image.fill(0xffffffff)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.save(str(Path(folder)/'layout-preview.png'))
    return len(pages)


def export_layout(document, result, settings, path, atlas=False):
    from PySide6.QtGui import QGuiApplication
    application = render_application()
    return write_layout(document, result, settings, path, atlas=atlas)


class WorkspaceBackend(Backend):
    layoutPrepared = Signal()
    workspacePanelRequested = Signal(str)
    exportFrameReady = Signal()
    detailRefreshRequested = Signal()
    externalFileRequested = Signal(QUrl)
    historyRestored = Signal()
    lineStyleSampled = Signal()
    groupsMatched = Signal()

    def __init__(self, *args, **kwargs):
        self.document = Document()
        from threading import Event
        self.update_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='geotrace-update')
        self.update_future=None;self.update_cancel=Event();self.update_progress=(0,0);self.update_release=None
        self.workspace_future = None
        self.workspace_kind = ''
        self.pending_document = None
        self._installing = False
        self.line_selection = []
        self.line_points = []
        self.line_preview = []
        self.line_drag = None
        self.layout_settings = None
        self.last_view = None
        self.detail_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='geotrace-detail')
        self.detail_future = None
        self.detail_pending = None
        self.detail_key = None
        self.detail_running = None
        self.line_continue = None
        self.matching_a={}
        self.matching_b={}
        self.matching_apply=False
        self.polygon_epoch=0
        self.polygon_view_key=None
        self.rebuild_selected_ids=[]
        super().__init__(*args, **kwargs)
        from .detail_tiles import DetailTiles
        self.detail_tiles=DetailTiles(self.detail_pool,Path(self.temp.name)/'detail-tiles')
        self.prepare_on_import = False
        recent = self.settings.value('workspace/recent', [], type=list)
        self.notify(workspaceEnabled=True, sheets=[], projectClasses=[], activeSheet='', lineTypes=[], lineCount=0,
                    lineMode=False, lineTool='select', lineVisible=True, lineLocked=False, polygonsLocked=False,
                    linesUrl='', lineDraft=[], lineSelected=[], lineSelection={}, lineSelectionPaths=[], lineFields=[], linePreviewCount=0,
                    paletteInputUrl='', paletteWidth=1, paletteHeight=1, selectionMeasurement='',
                    summary={}, layoutPreviewUrl='', layoutPages=0, layoutTemplates=[], recentProjects=recent,
                    classMappings=[], checkpointItems=[], workspacePanel='',
                    polygonPaths=[],polygonEpoch=0,drawClassId=0,groupFocusId=0,hiddenClassIds=[],
                    editStrokeWidth=float(self.settings.value('editor/strokeWidth',1.4)),
                    detailUrl='', detailRect={}, detailLabels=[],detailTiles=[], lineDragPreview=[],
                    updateState='idle',updateMessage='Проверьте наличие новой версии.',updateProgress=0,updateVersion='',updateNotes='',
                    groupMatching=False,matchingSide='A',matchingA={},matchingB={},matchingUrl='',
                    status='Создайте проект участка: добавьте KML и листы карты.')

    def task(self, kind, function, *args, quality=False):
        if self.data['busy']:
            return False
        self.workspace_kind = kind
        if quality:
            import time
            self.quality_started = time.monotonic()
            for name in ('quality-cancel', 'quality-progress.json'):
                (Path(self.temp.name)/name).unlink(missing_ok=True)
        self.workspace_future = self.pool.submit(function, *args)
        self.kind = 'quality_segment' if kind == 'recognise' else kind
        self.notify(busy=True, error=False, qualityBusy=quality, qualityCancelPending=False, qualityProgress=0,
                    status={'import':'Добавляю листы и совмещаю координаты…', 'open':'Открываю проект участка…',
                    'rebuild':'Собираю общий результат…', 'prepare':'Готовлю полный лист…', 'recognise':'Оцифровываю лист…',
                    'recognise_all':'Оцифровываю листы по очереди…', 'save':'Сохраняю проект участка…',
                    'autosave_workspace':'Создаю копию восстановления…', 'export_workspace':'Сохраняю слои GeoPackage…',
                    'detect_lines':'Бета-поиск линейных объектов…', 'layout':'Готовлю макет…', 'export_layout':'Экспортирую макет…',
                    'smooth':'Сглаживаю общие границы…', 'checkpoint':'Создаю контрольное сохранение…', 'seams':'Проверяю стыки и покрытие участка…'}[kind])
        return True

    def _install(self, output, fit=False):
        document, canvas, result = output[:3]
        self.detail_key = None
        self.detail_pending = None
        self.detail_running = None
        if self.detail_tiles.reset(document,canvas):self.notify(detailUrl='',detailLabels=[],detailTiles=[])
        self._installing = True
        try:
            self.document, self.raster = document, canvas
            self.view = dict(document.view)
            self.revision += 1
            self.reset_basemap()
            self.points, self.roi, self.proposal = [], None, None
            self.set_result(result)
            self.refresh_area()
            self.notify(width=canvas.rgb.shape[1], height=canvas.rgb.shape[0], rasterUrl=self.url('raster.png'),
                        filename=' · '.join(s.name for s in document.sheets), metadata=f'EPSG:3857 · {len(document.sheets)} листов · геодезические измерения',
                        layerSettings=dict(self.view), hintUrl='', hintCount=0, proposalReady=False)
            self.publish_document()
            if fit:
                self.mapLoaded.emit()
        finally:
            self._installing = False

    @Slot()
    def poll(self):
        if getattr(self,'_closing',False):return
        self.poll_updates()
        self.poll_detail()
        if self.workspace_future is None or not self.workspace_future.done():
            super().poll()
            return
        future, self.workspace_future = self.workspace_future, None
        kind = self.workspace_kind
        try:
            output = future.result()
            if self.data['qualityBusy'] and (Path(self.temp.name)/'quality-cancel').exists():
                raise Cancelled('Расчёт остановлен. Прежние данные сохранены.')
            if kind in ('import', 'open', 'rebuild'):
                self.clear_auto_preview()
                self.reset_quality()
                self._install(output, fit=kind in ('import', 'open'))
                if kind == 'open':
                    recovering = getattr(self, 'is_recovery', False)
                    self.project_path = self.document.source_project if recovering else self.load_target
                    if recovering:
                        self.restored_path = Path(self.load_target)
                        self.document.source_project = self.project_path
                    self.is_recovery = False
                    self.undo_stack.clear()
                    self.redo_stack.clear()
                    self.autosaved_revision = self.edit_revision
                    if recovering:
                        self.autosaved_revision=self.edit_revision-1
                    self.notify(projectPath=self.project_path, projectName=Path(self.project_path).name if self.project_path else 'Восстановленный проект', dirty=recovering,
                                canUndo=False, canRedo=False, autosaveStatus='Проект открыт')
                    if self.project_path:
                        self.remember_recent(self.project_path)
                self.notify(status=getattr(self,'rebuild_note','') or 'Листы совмещены. Выберите лист для подготовки или сопоставьте его группы.')
                self.rebuild_note=''
                if kind=='rebuild' and self.matching_apply:
                    self.matching_apply=False
                    self.groupsMatched.emit()
                if kind=='rebuild' and self.rebuild_selected_ids:
                    ids=set(self.rebuild_selected_ids);self.rebuild_selected_ids=[]
                    self.set_edit_selection([i for i,f in enumerate(self.result.features) if f['properties']['object_id'] in ids and self.feature_visible(f)])
            elif kind == 'prepare':
                self.base_palette = output['palette']
                self.quality_original = copy.deepcopy(self.base_palette)
                self.quality_groups = list(range(1, len(self.base_palette)+1))
                self.palette_labels = np.load(Path(self.temp.name)/'palette-labels.npy', allow_pickle=False)
                sheet = self.document.sheet()
                if sheet.recipe:
                    from .palette import split_labels
                    self.palette_samples=copy.deepcopy(sheet.recipe.get('samples',[]))
                    self.quality_groups=list(sheet.recipe.get('groups',self.quality_groups))
                    self.quality_original=copy.deepcopy(sheet.recipe.get('palette',self.base_palette))
                    if self.palette_samples:
                        self.sample_lab=np.load(Path(self.temp.name)/'palette-lab.npy',allow_pickle=False)
                        for sample in self.palette_samples:
                            self.palette_labels=split_labels(self.sample_lab,self.palette_labels,sample)
                self.revision += 1
                self.update_palette()
                self.notify(qualityReady=True, paletteInputUrl=self.url('palette-source.png'),
                            paletteWidth=sheet.raster.rgb.shape[1], paletteHeight=sheet.raster.rgb.shape[0],
                            qualityNote=f"{sheet.name} · полный лист · {output['source_note']}",
                            qualityEvidenceUrl=self.url('quality-evidence.png'), status='Полный лист подготовлен; KML применяется к общему результату.')
                QTimer.singleShot(0, self.palettePrepared.emit)
            elif kind in ('recognise','recognise_all'):
                self.pending_document, canvas, self.auto_preview, info = output
                self.auto_revision = self.edit_revision
                self.review_sites, self.review_index = info['sites'], -1
                self.revision += 1
                self.notify(autoReady=True, autoPreviewUrl=self.url('auto-preview.png'), reviewCount=len(self.review_sites),
                            autoSummary=f'Результат участка: {len(self.auto_preview.features)} полигонов',
                            status='Предпросмотр листа в общем проекте. Примените результат после проверки.')
            elif kind in ('save', 'autosave_workspace'):
                self.autosaved_revision = self.edit_revision
                self.view_dirty = False
                if kind == 'save':
                    self.project_path = self.save_target
                    self.remove_active_recovery(include_restored=True)
                    self.remember_recent(self.project_path)
                    self.notify(dirty=False, projectPath=self.project_path, projectName=Path(self.project_path).name,
                                autosaveStatus='Проект сохранён', status='Проект участка сохранён.')
                    QTimer.singleShot(0, self.projectSaved.emit)
                else:
                    self.notify(autosaveStatus='Копия восстановления · '+datetime.now().strftime('%H:%M'),
                                status=getattr(self,'autosave_previous_status','Готово.'))
            elif kind == 'detect_lines':
                self.line_preview = output
                self.notify(linePreviewCount=len(output), status=f'Найдено линий: {len(output)}. Проверьте предложения перед применением.')
                self.paint_line_preview()
            elif kind == 'smooth':
                if getattr(self,'smooth_cancelled',False):raise Cancelled('Предпросмотр сглаживания отменён.')
                output,info=output
                self.edit_preview=output if info['changed'] else None
                self.geometry_operation='smooth';self.geometry_primary_id=None
                message=(f"Изменено полигонов: {info['changed']}. Смещение до {info['max_shift_pixels']:.2f} пикселя исходника. Общие границы и покрытие сохранены." if info['changed'] else 'В выбранной области нет границ, которые можно безопасно сгладить с этой силой.')
                if info['limited']:message+=' Сила ограничена проверкой геометрии.'
                self.notify(smoothInfo=message,geometryReady=bool(info['changed']),geometryPreviewVisible=True,
                    editPreviewUrl=self.url('edit-preview.png') if info['changed'] else '',editSelectionUrl='',status=message)
            elif kind == 'layout':
                self.revision += 1
                self.notify(layoutPreviewUrl=self.url('layout-preview.png'), layoutPages=output, status='Макет готов к просмотру.')
                self.layoutPrepared.emit()
            elif kind == 'checkpoint':
                self.refresh_checkpoints()
                self.notify(status='Контрольное сохранение создано.')
            elif kind == 'seams':
                self.review_sites, self.review_index = output, -1
                self.notify(reviewCount=len(output), status=f'Участков для проверки: {len(output)}.')
            else:
                self.notify(status=f'Сохранено: {output}')
        except Cancelled as exc:
            self.matching_apply=False
            self.rebuild_note=''
            self.notify(status=str(exc), error=False)
        except Exception as exc:
            self.matching_apply=False
            self.rebuild_note=''
            self.notify(status=str(exc), error=True)
        finally:
            self.notify(busy=False, qualityBusy=False, qualityCancelPending=False)
        super().poll()

    @Slot(QUrl)
    def openFile(self, url):
        path = url.toLocalFile()
        if path.lower().endswith('.kml'):
            self.loadArea(url)
        elif path.lower().endswith('.geotrace'):
            if self.data['busy']:
                return
            self.load_target = path
            self.is_recovery = False
            self.task('open', open_workspace, path, self.temp.name)
        else:
            self.addSheets([url])

    @Slot('QVariantList')
    def addSheets(self, urls):
        if self.data['busy']:
            return
        paths = [u.toLocalFile() if isinstance(u, QUrl) else QUrl(str(u)).toLocalFile() or str(u) for u in urls]
        if not paths:
            return
        self.record_undo(None)
        self.task('import', import_sheets, self.document.clone(), paths, self.temp.name)

    @Slot(str)
    def activateSheet(self, identity):
        if self.data['busy'] or self.document.sheet(identity) is None:
            return
        self.document.active_sheet = identity
        self.reset_quality()
        self.clear_auto_preview()
        self.publish_document()
        self.notify(status=f'Активный лист: {self.document.sheet().name}. Подготовка использует весь лист.')

    @Slot(str)
    def removeSheet(self, identity):
        if self.data['busy'] or len(self.document.sheets) <= 1:
            self.notify(status='В проекте должен остаться хотя бы один лист.', error=True)
            return
        self.record_undo(None)
        doc = self.document.clone()
        doc.sheets = [s for s in doc.sheets if s.id != identity]
        doc.active_sheet = doc.sheets[0].id
        self.task('rebuild', build_workspace, doc, self.temp.name)

    @Slot(str, bool)
    def setSheetVisible(self, identity, value):
        if self.data['busy'] or self.document.sheet(identity) is None:
            return
        self.record_undo(None)
        doc = self.document.clone()
        doc.sheet(identity).visible = value
        self.task('rebuild', build_workspace, doc, self.temp.name)

    @Slot(bool)
    def setSmoothScanSeams(self, value):
        if self.data['busy']:return
        doc=self.document.clone()
        doc.settings['smooth_scan_seams']=value
        doc.manual=_copy_result(self.result)
        self._group_change(doc,'Отображение стыков сканов обновлено. Координаты и объекты сохранены.')

    @Slot(QUrl)
    def loadArea(self, url):
        if self.data['busy']:
            return
        try:
            from types import SimpleNamespace
            self.pending_areas = read_kml(url.toLocalFile(), SimpleNamespace(crs_wkt=CRS.from_epsg(3857).to_wkt()))
            self.notify(areaOptions=[name for name, _ in self.pending_areas])
            self.areaPrepared.emit()
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot(int)
    def previewArea(self, index):
        if not 0 <= index < len(self.pending_areas):
            return
        if self.raster is None:
            self.notify(status='Участок выбран. После добавления листов появится его карта.')
            return
        from .region import set_area
        try:
            name, geometry = self.pending_areas[index]
            candidate = set_area(self.raster, geometry, name)
            save_preview(candidate, Path(self.temp.name)/'area-draft-raster.png')
            save_area_preview(candidate, candidate.area, Path(self.temp.name)/'area-draft.png')
            self.revision += 1
            self.notify(areaDraftUrl=self.url('area-draft.png'), areaDraftRasterUrl=self.url('area-draft-raster.png'))
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot(int)
    def applyArea(self, index):
        if self.data['busy'] or not 0 <= index < len(self.pending_areas):
            return
        name, geometry = self.pending_areas[index]
        self.record_undo(None)
        doc = self.document.clone()
        # Preserve each individual contour for atlas generation.
        doc.areas = [dict(name=n, geometry=g) for n, g in self.pending_areas]
        doc.active_area = index
        if not doc.sheets:
            self.document = doc
            self.notify(areaName=name, areaOptions=[], status='KML добавлен. Теперь добавьте листы карты.')
            self.mark_dirty()
            return
        self.task('rebuild', build_workspace, doc, self.temp.name)

    @Slot()
    def clearArea(self):
        if not self.data['busy'] and self.document.sheets:
            self.record_undo(None)
            doc = self.document.clone()
            doc.active_area = -1
            self.task('rebuild', build_workspace, doc, self.temp.name)

    def refresh_area(self):
        if self.raster is None:
            return
        save_area_preview(self.raster, self.document.area, Path(self.temp.name)/'area.png')
        self.notify(areaName=self.document.areas[self.document.active_area]['name'] if self.document.area is not None else '',
                    areaUrl=self.url('area.png') if self.document.area is not None else '', areaDraftUrl='', areaDraftRasterUrl='', areaOptions=[])

    @Slot()
    def prepareQuality(self):
        if self.document.sheet() is None or self.data['busy']:
            return
        self.clear_auto_preview()
        self.reset_quality()
        self.quality_token = uuid.uuid4().hex
        self.task('prepare', prepare_sheet, self.document.sheet().raster, self.quality_token, self.temp.name, quality=True)

    @Slot(float, float, float, float)
    def setSheetFrame(self, x0, y0, x1, y1):
        if self.data['busy'] or self.document.sheet() is None:
            return
        try:
            from .region import set_area
            doc = self.document.clone()
            sheet = doc.sheet()
            geometry = transform_geometry(box(x0, y0, x1, y1), sheet.raster.transform)
            sheet.raster = set_area(sheet.raster, geometry, 'Рамка листа')
            self.record_undo(None)
            self.reset_quality()
            self.task('rebuild', build_workspace, doc, self.temp.name)
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot()
    def resetSheetFrame(self):
        if self.data['busy'] or self.document.sheet() is None:
            return
        from dataclasses import replace
        doc = self.document.clone()
        sheet = doc.sheet()
        if sheet.raster.source_valid is None:
            return
        sheet.raster = replace(sheet.raster, valid=sheet.raster.source_valid, source_valid=None, area=None, area_name='')
        self.record_undo(None)
        self.reset_quality()
        self.task('rebuild', build_workspace, doc, self.temp.name)

    @contextmanager
    def palette_source(self):
        previous = self.raster
        if self.document.sheet() is not None:
            self.raster = self.document.sheet().raster
        try:
            yield
        finally:
            self.raster = previous

    @Slot(int, int, result=int)
    def samplePalette(self, x, y):
        with self.palette_source():
            return super().samplePalette(x, y)

    @Slot(int)
    def focusPalette(self, identity):
        with self.palette_source():
            super().focusPalette(identity)

    @Slot()
    def previewQuality(self):
        if self.polygon_locked():
            return
        if self.document.sheet() is None or self.data['busy']:
            return
        if not self.data['qualityReady']:
            self.prepareQuality()
            return
        if not any(self.quality_groups):
            self.notify(status='Оставьте хотя бы одну целевую заливку.', error=True)
            return
        self.auto_revision = self.edit_revision
        self.task('recognise', recognise_sheet, self.document.clone(), self.quality_token,
                  list(self.quality_groups), self.temp.name, list(self.palette_samples), list(self.quality_original), quality=True)

    @Slot()
    def digitiseAll(self):
        if self.polygon_locked():
            return
        if self.document.sheets and not self.data['busy']:
            self.auto_revision=self.edit_revision
            self.task('recognise_all', recognise_all, self.document.clone(), self.temp.name, quality=True)

    @Slot()
    def acceptAuto(self):
        if self.polygon_locked():
            return
        if self.pending_document is None:
            super().acceptAuto()
            return
        if self.data['busy'] or self.auto_preview is None or self.auto_revision != self.edit_revision:
            return
        self.record_undo(None)
        document, result = self.pending_document, self.auto_preview
        self.pending_document = None
        self.document = document
        save_preview(result.raster, Path(self.temp.name)/'raster.png')
        preview_overlay(document, result, Path(self.temp.name)/'overlay.png', Path(self.temp.name)/'lines.png')
        self.auto_preview = None
        self._install((document, result.raster, result))
        self.notify(autoReady=False, autoPreviewUrl='', status='Результат листа применён. Можно сопоставить группы соседних листов.')

    def set_result(self, output):
        from .topology import clean_result_interiors
        output=clean_result_interiors(output)
        self.polygon_epoch+=1
        self.polygon_view_key=None
        if output is not None and self.document.sheets:
            self.document.fields = copy.deepcopy(output.fields)
            while len(self.document.classes) < len(output.colors):
                index = len(self.document.classes)
                self.document.classes.append(dict(id=index+1, name=f'Класс {index+1}',
                    style=validate_style(dict(fill='#%02x%02x%02x' % tuple(output.colors[index])))))
            preview_overlay(self.document, output, Path(self.temp.name)/'overlay.png', Path(self.temp.name)/'lines.png')
        super().set_result(output)
        self.notify(polygonEpoch=self.polygon_epoch)
        if self.document.sheets and output is not None:
            self.publish_document()

    def mark_dirty(self):
        super().mark_dirty()
        if not self._installing and self.result is not None:
            self.document.manual = _copy_result(self.result)
            self.document.fields = copy.deepcopy(self.result.fields)
        self.document.view = dict(self.view)
        self.detailRefreshRequested.emit()

    def refresh_selection(self):
        super().refresh_selection()
        if self.result is not None and 0 <= self.selected < len(self.result.features):
            indices = self.edit_selected or [self.selected]
            area = sum(measurements(self.result.features[i]['geometry'])['area_m2'] for i in indices)
            self.notify(selectionMeasurement=f'{area/10000:,.3f} га · {area/1e6:,.4f} км²'.replace(',', ' '))
        else:
            self.notify(selectionMeasurement='')

    def feature_visible(self,feature):
        return feature['properties']['class_id'] not in self.document.settings.get('hidden_classes',[])

    @Slot(float,float,float,float,float)
    @Slot(float,float,float,float,float,bool)
    def requestPolygonVectors(self,x,y,width,height,zoom,show_auto=False):
        if not np.isfinite((x,y,width,height,zoom)).all() or min(width,height,zoom)<=0:return
        result=self.edit_preview if self.data.get('geometryReady') and self.data.get('geometryPreviewVisible',True) and self.edit_preview is not None else self.result
        if show_auto and self.auto_preview is not None:result=self.auto_preview
        selected=[self.result.features[i]['properties']['object_id'] for i in self.edit_selected] if self.result else []
        if not selected and self.result is not None and 0<=self.selected<len(self.result.features):selected=[self.result.features[self.selected]['properties']['object_id']]
        if result is not self.result and result is not None:
            old={f['properties']['object_id']:f['geometry'] for f in self.result.features} if self.result else {}
            selected=[f['properties']['object_id'] for f in result.features if f['properties']['object_id'] not in old or not f['geometry'].equals_exact(old[f['properties']['object_id']],0)]
        key=(id(result),self.polygon_epoch,tuple(selected),x,y,width,height,zoom,id(self.edge_drag))
        if key==self.polygon_view_key:return
        from .display import polygon_paths
        doc=self.pending_document if show_auto and self.pending_document is not None else self.document
        cut_key=(id(result),id(doc),self.polygon_epoch)
        if getattr(self,'polygon_cut_key',None)!=cut_key:
            from .sheet_seams import technical_boundaries
            self.polygon_cuts=technical_boundaries(doc,result)
            self.polygon_cut_key=cut_key
        paths=polygon_paths(result,doc,(x,y,width,height),zoom,selected,self.edge_drag,self.tree if result is self.result else None,self.polygon_cuts)
        self.polygon_view_key=key
        self.notify(polygonPaths=paths)

    @Slot(result=bool)
    def beginSmoothing(self):
        if self.data['busy'] or self.data.get('autoReady') or (self.data.get('geometryReady') and getattr(self,'geometry_operation','')!='smooth'):
            self.notify(status='Сначала примените или закройте текущий предпросмотр.');return False
        self.notify(smoothingOpen=True,smoothInfo='',geometryPreviewVisible=True)
        return True

    @Slot(str,int,str,float)
    def previewSmoothing(self,scope,group,mode,strength):
        if self.data['busy'] or self.result is None or self.polygon_locked():return
        if scope not in ('selected','group','all'):
            self.notify(error=True,status='Выберите область сглаживания.');return
        identities=None
        if scope=='selected':identities={self.result.features[i]['properties']['object_id'] for i in (self.edit_selected or ([self.selected] if 0<=self.selected<len(self.result.features) else []))}
        elif scope=='group':identities={f['properties']['object_id'] for f in self.result.features if f['properties']['class_id']==group}
        if identities is not None and not identities:
            self.notify(smoothInfo='В выбранной области нет полигонов.');return
        self.smooth_cancelled=False
        (Path(self.temp.name)/'smooth-cancel').unlink(missing_ok=True)
        self.task('smooth',preview_smoothing,self.document.clone(),_copy_result(self.result),mode,strength,identities,self.temp.name)

    @Slot(bool)
    def showSmoothPreview(self,visible):
        self.notify(geometryPreviewVisible=visible)

    @Slot()
    def cancelSmoothing(self):
        if self.workspace_kind=='smooth' and self.workspace_future is not None:
            self.smooth_cancelled=True;(Path(self.temp.name)/'smooth-cancel').touch()
        if getattr(self,'geometry_operation','')=='smooth':self.invalidate_edit_preview()
        self.notify(smoothingOpen=False,geometryPreviewVisible=True)

    @Slot(float)
    def setEditStrokeWidth(self,width):
        if np.isfinite(width) and .5<=width<=4:
            self.settings.setValue('editor/strokeWidth',float(width))
            self.notify(editStrokeWidth=float(width))

    @Slot(int)
    def setDrawGroup(self,identity):
        if 0<=identity<=len(self.document.classes):
            self.document.settings['draw_class']=identity
            self.notify(drawClassId=identity)

    def _group_change(self,doc,status,focus=0):
        doc.dissolve_classes=False
        if doc.manual is not None:
            from .groups import palette
            doc.manual.colors=palette(doc)
        self.record_undo(None)
        self.rebuild_selected_ids=[f['properties']['object_id'] for i,f in enumerate(self.result.features) if i in self.edit_selected or i==self.selected] if self.result else []
        self.rebuild_note=status
        self.notify(groupFocusId=focus)
        self.task('rebuild',group_workspace,doc,self.temp.name)

    @Slot(str,str,result=bool)
    def createGroup(self,name,color):
        if self.data['busy']:return False
        try:
            from .groups import create_group
            doc=self.document.clone();doc.manual=_copy_result(self.result)
            identity=create_group(doc,name,color);doc.settings['draw_class']=identity
            self._group_change(doc,'Новая группа создана.',identity)
            return True
        except ValueError as exc:self.notify(error=True,status=str(exc));return False

    @Slot(int,int,result=bool)
    def deleteGroup(self,identity,target):
        if self.data['busy'] or self.polygon_locked():return False
        try:
            from .groups import remove_group
            doc=self.document.clone();doc.manual=_copy_result(self.result)
            focus=remove_group(doc,identity,target)
            self._group_change(doc,'Группа удалена. Её объекты сохранены в выбранной группе.',focus)
            return True
        except ValueError as exc:self.notify(error=True,status=str(exc));return False

    @Slot(int,result=bool)
    def setObjectsGroup(self,identity):
        if self.data['busy'] or self.polygon_locked() or self.result is None:return False
        if not 1<=identity<=len(self.document.classes):return False
        indices=self.edit_selected or ([self.selected] if self.selected>=0 else [])
        if not indices:return False
        doc=self.document.clone();doc.manual=_copy_result(self.result)
        for i in indices:doc.manual.features[i]['properties'].update(class_id=identity,origin='manual_group',review='edited')
        from .editing import _result_from_features
        doc.manual=_result_from_features(self.raster,doc.manual.features,doc.manual.colors,doc.manual.fields)
        self._group_change(doc,f'Группа изменена у {len(indices)} объектов.',identity)
        return True

    @Slot(int,bool)
    def setGroupVisible(self,identity,visible):
        if self.data['busy'] or not 1<=identity<=len(self.document.classes):return
        doc=self.document.clone();doc.manual=_copy_result(self.result)
        hidden=set(doc.settings.get('hidden_classes',[]))
        if visible:hidden.discard(identity)
        else:hidden.add(identity)
        doc.settings['hidden_classes']=sorted(hidden)
        self._group_change(doc,'Видимость группы изменена.',identity)

    @Slot(int)
    def focusGroup(self,identity):
        self.notify(groupFocusId=identity)

    @Slot(str, int, int, str)
    def assignGroup(self, sheet_id, local, class_id, name):
        if self.data['busy']:
            return
        try:
            doc = self.document.clone()
            doc.assign_group(sheet_id, local, class_id, name)
            self.record_undo(None)
            self.task('rebuild', build_workspace, doc, self.temp.name)
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot(bool)
    def stitchClasses(self, enabled):
        self.notify(status='Автоматическая сшивка временно отключена. Сопоставление групп доступно без изменения геометрии.')

    @Slot(int, str, str)
    def styleClass(self, identity, name, encoded):
        if self.data['busy']:
            return
        try:
            style = validate_style(json.loads(encoded))
            doc = self.document.clone()
            if not 1<=identity<=len(doc.classes):raise ValueError('Группа не найдена.')
            item = doc.classes[identity-1]
            item['name'], item['style'] = name.strip() or item['name'], style
            if len(item['name'])>200:raise ValueError('Название группы не должно превышать 200 символов.')
            doc.manual=_copy_result(self.result)
            self._group_change(doc,'Оформление группы обновлено.',identity)
        except (ValueError, IndexError, TypeError) as exc:
            self.notify(error=True, status=str(exc))

    def publish_document(self):
        if not hasattr(self, 'data'):
            return
        mappings = []
        for s in self.document.sheets:
            if s.result:
                mappings.extend(dict(sheet=s.id, sheetName=s.name, group=local, classId=cid,
                    color='#%02x%02x%02x' % tuple(s.result.colors[local-1]),
                    matched=f'{s.id}:{local}' in self.document.settings.get('matched_groups',[])) for local, cid in s.groups.items())
        self.notify(sheets=[dict(id=s.id, name=s.name, path=s.raster.path, visible=s.visible, locked=s.locked,
                                processed=s.result is not None, polygons=len(s.result.features) if s.result else 0) for s in self.document.sheets],
                    activeSheet=self.document.active_sheet, projectClasses=copy.deepcopy(self.document.classes),
                    smoothScanSeams=self.document.settings.get('smooth_scan_seams',True),
                    classMappings=mappings, lineTypes=copy.deepcopy(self.document.line_types), lineCount=len(self.document.lines),
                    lineFields=copy.deepcopy(self.document.line_fields), linesUrl=self.url('lines.png') if self.document.lines else '',
                    layoutTemplates=[dict(name=l['name'], index=i) for i, l in enumerate(self.document.layouts)],
                    summary=self.document.summary(self.result) if self.result is not None else {},
                    fitBounds=list(transform_geometry(self.raster.area,~self.raster.transform).bounds) if self.raster is not None and self.document.area is not None else [],
                    stitching=False,hiddenClassIds=self.document.settings.get('hidden_classes',[]),
                    drawClassId=self.document.settings.get('draw_class',0),
                    canUndo=bool(self.undo_stack), canRedo=bool(self.redo_stack))
        valid_types = {t['id'] for t in self.document.line_types}
        self.notify(currentLineType=self.data.get('currentLineType') if self.data.get('currentLineType') in valid_types else next(iter(sorted(valid_types)), 0),
                    lineVisible=self.document.settings.get('lineVisible', True),
                    lineLocked=self.document.settings.get('lineLocked', False),
                    polygonsLocked=self.document.settings.get('polygonsLocked', False),
                    colors=[dict(label=c['name'], color=c['style']['fill'], count=sum(f['properties']['class_id']==c['id'] for f in self.result.features) if self.result else 0) for c in self.document.classes])
        if self.data.get('groupMatching'):
            from .group_matching import group_record
            try:
                self.matching_a=group_record(self.document,self.matching_a.get('sheet',''),self.matching_a.get('group',0))
                self.matching_b=group_record(self.document,self.matching_b.get('sheet',''),self.matching_b.get('group',0))
            except ValueError:self.matching_a,self.matching_b={},{}
            self.notify(matchingA=self.matching_a,matchingB=self.matching_b)

    @Slot(QUrl)
    def saveProject(self, url):
        self._save(url, False)

    @Slot(QUrl)
    def savePortable(self, url):
        self._save(url, True)

    def _save(self, url, portable):
        if self.data['busy'] or not self.document.sheets:
            return
        path = url.toLocalFile() or self.project_path
        if not path:
            return
        if not path.lower().endswith('.geotrace'):
            path += '.geotrace'
        self.save_target = str(Path(path).resolve())
        self.document.source_project = self.save_target
        self.document.view = dict(self.view)
        if self.result is not None:
            self.document.manual = _copy_result(self.result)
        self.task('save', save_document, self.save_target, self.document.clone(), self.raster, self.result, portable)

    @Slot()
    def autosave(self):
        if (not self.document.sheets or self.autosave_paused or self.data['busy']
                or (self.edit_revision == self.autosaved_revision and not self.view_dirty)):
            return
        self.document.source_project = self.project_path
        self.document.view = dict(self.view)
        self.document.manual = _copy_result(self.result)
        self.autosave_previous_status=self.data.get('status','Готово.')
        self.task('autosave_workspace', save_document, str(self.recovery_path), self.document.clone(), self.raster, self.result)

    @Slot(str)
    def restoreRecovery(self, path):
        if not self.data['busy'] and path in {i['path'] for i in self.data['recoveries']}:
            self.load_target = path
            self.is_recovery = True
            self.task('open', open_workspace, path, self.temp.name)

    def remember_recent(self, path):
        items = self.settings.value('workspace/recent', [], type=list)
        items = [path]+[p for p in items if p != path]
        self.settings.setValue('workspace/recent', items[:8])
        self.notify(recentProjects=items[:8])

    @Slot(str)
    def openRecent(self, path):
        self.openFile(QUrl.fromLocalFile(path))

    @Slot(QUrl)
    def exportFile(self, url):
        if self.result is not None and not self.data['busy']:
            path = url.toLocalFile()
            if not path.lower().endswith('.gpkg'):
                path += '.gpkg'
            self.task('export_workspace', export_workspace, self.document.clone(), self.result, path)

    def record_undo(self, entry):
        # One transaction includes source sheets, class mappings, lines and edits.
        if not hasattr(self, 'data'):
            return
        super().record_undo(('workspace', (self.document.clone(), self.history_snapshot())))
        self.undo_stack = self.undo_stack[-12:]

    @Slot()
    def undo(self):
        if self.data['busy'] or not self.undo_stack:
            return
        kind, previous = self.undo_stack[-1]
        if kind != 'workspace':
            return super().undo()
        self.redo_stack.append((self.document.clone(), self.history_snapshot()))
        self.undo_stack.pop()
        self.restoring_history = True
        try:
            self.restore_workspace(previous)
        finally:
            self.restoring_history = False

    @Slot()
    def redo(self):
        if self.data['busy'] or not self.redo_stack:
            return
        self.undo_stack.append(('workspace', (self.document.clone(), self.history_snapshot())))
        self.restoring_history = True
        try:
            self.restore_workspace(self.redo_stack.pop())
        finally:
            self.restoring_history = False

    def restore_workspace(self, snapshot):
        doc, old = snapshot
        self.document = doc
        self.line_drag=None
        self.line_points=[]
        self.line_continue=None
        self.line_preview=[]
        self.notify(lineDraft=[],lineDragPreview=[],linePreviewUrl='',linePreviewCount=0)
        if old[0] is not None:
            self._installing = True
            try:
                self.restore_snapshot(old)
            finally:
                self._installing = False
        else:
            self.raster, self.result = None, None
            self.notify(rasterUrl='', overlayUrl='', detailUrl='', width=1, height=1, count=0,
                        areaName=doc.areas[doc.active_area]['name'] if doc.area is not None else '')
        self.mark_dirty()
        self.publish_document()
        self.publish_line_selection()
        self.historyRestored.emit()
        self.notify(status='Изменение отменено или повторено.', error=False)

    @Slot(str)
    def createCheckpoint(self, title):
        if not self.document.sheets or self.data['busy']:
            return
        key = self.document.settings.get('checkpoint_id', '')
        if not isinstance(key, str) or len(key) != 32 or not key.isalnum():
            key = uuid.uuid4().hex
            self.document.settings['checkpoint_id'] = key
        folder = self.recovery_dir.parent/'checkpoints'/key
        filename = datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.geotrace'
        self.document.settings['checkpoint_title'] = title.strip()[:100]
        self.mark_dirty()
        self.task('checkpoint', save_document, str(folder/filename), self.document.clone(), self.raster, self.result)

    def refresh_checkpoints(self):
        key = self.document.settings.get('checkpoint_id', '')
        if not key or not key.isalnum():
            self.notify(checkpointItems=[])
            return
        folder = self.recovery_dir.parent/'checkpoints'/key
        import zipfile
        items = []
        for p in sorted(folder.glob('*.geotrace'), reverse=True):
            try:
                with zipfile.ZipFile(p) as archive:
                    info = archive.getinfo('workspace.json')
                    if info.file_size > 8_000_000:
                        continue
                    title = json.loads(archive.read(info)).get('settings', {}).get('checkpoint_title', '')
                items.append(dict(path=str(p), name=(title+' · ' if title else '')+datetime.fromtimestamp(p.stat().st_mtime).strftime('%d.%m.%Y %H:%M')))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
        self.notify(checkpointItems=items)

    @Slot()
    def refreshCheckpoints(self):
        self.refresh_checkpoints()

    @Slot(str, int)
    def moveSheet(self, identity, direction):
        if self.data['busy']:
            return
        doc = self.document.clone()
        index = next((i for i, s in enumerate(doc.sheets) if s.id == identity), -1)
        target = index+(1 if direction > 0 else -1)
        if index < 0 or not 0 <= target < len(doc.sheets):
            return
        self.record_undo(None)
        doc.sheets[index], doc.sheets[target] = doc.sheets[target], doc.sheets[index]
        self.task('rebuild', build_workspace, doc, self.temp.name)

    @Slot(str, bool)
    def assignGroups(self, encoded, stitch):
        if self.data['busy']:
            return
        try:
            records = json.loads(encoded)
            doc = self.document.clone()
            for row in records:
                doc.assign_group(row['sheet'], int(row['group']), int(row['classId']), str(row.get('newName', '')))
            doc.dissolve_classes = stitch
            self.record_undo(None)
            self.task('rebuild', build_workspace, doc, self.temp.name)
        except (ValueError, TypeError, KeyError) as exc:
            self.notify(error=True, status=str(exc))

    @Slot()
    def beginGroupMatching(self):
        from .group_matching import group_record
        sheets=[s for s in self.document.sheets if s.result is not None]
        if not sheets:
            self.notify(error=True,status='Сначала примените оцифровку листов, затем сопоставьте группы.')
            return
        self.matching_a=group_record(self.document,sheets[0].id)
        self.matching_b=group_record(self.document,sheets[min(1,len(sheets)-1)].id)
        self.setLineMode(False)
        self.notify(groupMatching=True,matchingSide='A',matchingA=self.matching_a,matchingB=self.matching_b,
                    status='Выберите группу A на карте, затем группу B. Можно также выбрать заливку в карточке листа.',error=False)

    @Slot()
    def endGroupMatching(self):
        self.notify(groupMatching=False,matchingUrl='')

    @Slot(str)
    def setMatchingSide(self,side):
        if side in ('A','B'):self.notify(matchingSide=side)

    @Slot(str,str,int)
    def chooseMatchingGroup(self,side,sheet_id,group):
        if self.data['busy'] or side not in ('A','B'):return
        from .group_matching import group_record,paint_groups
        try:
            if group and f'{sheet_id}:{group}' in self.document.settings.get('matched_groups',[]):
                raise ValueError('Эта группа уже сопоставлена. Выберите следующую несопоставленную заливку.')
            record=group_record(self.document,sheet_id,group)
            if side=='A':self.matching_a=record
            else:self.matching_b=record
            self.notify(matchingA=self.matching_a,matchingB=self.matching_b)
            if self.raster is not None:
                paint_groups(self.document,self.raster,self.matching_a,self.matching_b,Path(self.temp.name)/'matching.png')
                self.revision+=1;self.notify(matchingUrl=self.url('matching.png'))
        except ValueError as exc:self.notify(error=True,status=str(exc))

    @Slot(float,float)
    def pickMatchingGroup(self,x,y):
        if self.raster is None or self.data['busy'] or not self.data.get('groupMatching'):return
        from .group_matching import group_at
        side=self.data['matchingSide']
        record=group_at(self.document,*self.world(x,y),(self.matching_a if side=='A' else self.matching_b).get('sheet',''))
        if record is None:
            self.notify(status='В этой точке нет распознанной группы. Выберите заливку внутри полигона.')
            return
        side=self.data['matchingSide']
        self.chooseMatchingGroup(side,record['sheet'],record['group'])
        self.notify(matchingSide='B' if side=='A' else 'A',error=False)

    @Slot(int,str)
    def applyMatchedGroups(self,target,name):
        if self.data['busy']:return
        try:
            doc=self.document.clone()
            doc.manual=_copy_result(self.result)
            records=[r for r in (self.matching_a,self.matching_b) if r.get('group')]
            if not records:return
            from .groups import match_groups
            target=match_groups(doc,records,target,name)
            from .group_matching import group_record
            self.matching_a=group_record(doc,self.matching_a.get('sheet',''))
            self.matching_b=group_record(doc,self.matching_b.get('sheet',''))
            self.matching_apply=True
            self.notify(matchingSide='A',matchingA=self.matching_a,matchingB=self.matching_b,matchingUrl='')
            self._group_change(doc,'Группы сопоставлены. Дубликаты удалены, обработанные заливки скрыты из списка.',target)
        except ValueError as exc:self.notify(error=True,status=str(exc))

    @Slot()
    def stitchMatchedGroups(self):
        self.stitchClasses(False)

    @Slot(str)
    def restoreCheckpoint(self, path):
        self.refresh_checkpoints()
        if not self.data['busy'] and path in {r['path'] for r in self.data['checkpointItems']}:
            self.record_undo(None)
            self.task('rebuild', open_workspace, path, self.temp.name)

    @Slot(bool)
    def setLineMode(self, enabled):
        self.line_points = []
        self.line_selection = []
        self.line_drag=None
        self.line_continue=None
        self.clearEditSelection()
        self.notify(lineMode=enabled, lineDraft=[], lineSelection={}, lineSelected=[],lineSelectionUrl='',lineDragPreview=[],lineSelectionPaths=[])

    @Slot(str)
    def setLineTool(self, tool):
        if tool not in ('select', 'draw', 'drag', 'vertex', 'split', 'insert', 'remove_vertex'):
            return
        self.line_points = []
        self.line_continue = None
        self.notify(lineTool=tool, lineDraft=[])

    @Slot(str, bool)
    def setWorkspaceLayer(self, key, value):
        if key not in ('lineVisible', 'lineLocked', 'polygonsLocked') or self.data['busy']:
            return
        self.record_undo(None)
        self.document.settings[key] = value
        self.notify(**{key:value})
        self.mark_dirty()

    @Slot(str, result=bool)
    def saveLineType(self, encoded):
        if self.data['busy']:
            return False
        try:
            item = json.loads(encoded)
            if not item.get('id'):
                item['id'] = max((t['id'] for t in self.document.line_types), default=0)+1
            item = validate_type(item)
            self.record_undo(None)
            existing = next((i for i, t in enumerate(self.document.line_types) if t['id'] == item['id']), None)
            if existing is None:
                self.document.line_types.append(item)
            else:
                self.document.line_types[existing] = item
            self.notify(currentLineType=item['id'])
            self.line_changed()
            return True
        except (ValueError, TypeError, KeyError) as exc:
            self.notify(error=True, status=str(exc))
            return False

    @Slot(int)
    def chooseLineType(self, identity):
        if any(t['id'] == identity for t in self.document.line_types):
            self.notify(currentLineType=identity)

    @Slot(int, int, int, result=str)
    def sampleLineColour(self, x, y, identity):
        sheet = self.document.sheet()
        if sheet is None or self.data['busy']:
            return ''
        h, w = sheet.raster.valid.shape
        if not (0 <= x < w and 0 <= y < h and sheet.raster.valid[y, x]):
            return ''
        item = next((t for t in self.document.line_types if t['id'] == identity), None)
        if item is None:
            return ''
        color = '#%02x%02x%02x' % tuple(sheet.raster.rgb[y, x])
        if color not in item['colors']:
            proposed = dict(item, colors=item['colors']+[color])
            if not self.saveLineType(json.dumps(proposed)):
                return ''
        return color

    @Slot(float,float,int,bool,result=str)
    def sampleWorkspaceLineColor(self,x,y,identity,source_only=False):
        if self.raster is None or self.data['busy']:return ''
        row,col=math.floor(y),math.floor(x)
        h,w=self.raster.valid.shape
        if not 0<=row<h or not 0<=col<w or not self.raster.valid[row,col]:return ''
        item=next((t for t in self.document.line_types if t['id']==identity),None)
        if item is None:return ''
        world=self.world(x,y)
        from .detail import source_window
        for sheet in reversed(self.document.sheets):
            if not sheet.visible:continue
            pixel=abs(self.raster.transform.a)
            fine=source_window(sheet.raster,(world[0]-pixel,world[1]-pixel,world[0]+pixel,world[1]+pixel),side=64)
            if fine is None:continue
            point=project_geometry(Point(world),DISPLAY_CRS,fine.crs_wkt)
            col,row=(~fine.transform)*(point.x,point.y);col,row=math.floor(col),math.floor(row)
            h,w=fine.valid.shape
            if not 0<=col<w or not 0<=row<h or not fine.valid[row,col]:continue
            color='#%02x%02x%02x'%tuple(fine.rgb[row,col])
            proposed=copy.deepcopy(item)
            if not source_only:proposed['style']['color']=color
            if color not in proposed['colors']:proposed['colors']=(proposed['colors']+[color])[-12:]
            if self.saveLineType(json.dumps(proposed)):
                self.lineStyleSampled.emit()
                return color
        self.notify(status='Укажите цвет внутри видимой карты.',error=False)
        return ''

    @Slot()
    def detectLines(self):
        if self.data['busy'] or self.document.sheet() is None:
            return
        if not self.document.line_types:
            self.notify(error=True, status='Сначала добавьте тип линий и образец цвета.')
            return
        self.task('detect_lines', detect_sheet_lines, self.document.sheet().raster,
                  copy.deepcopy(self.document.line_types), self.temp.name, quality=True)

    def paint_line_preview(self):
        from PIL import ImageDraw
        image = Image.new('RGBA', (self.data['width'], self.data['height']))
        draw = ImageDraw.Draw(image)
        inverse = ~self.raster.transform
        for f in self.line_preview:
            draw.line([inverse*p for p in f['geometry'].coords], fill='#e32b8e', width=2)
        image.save(Path(self.temp.name)/'line-preview.png')
        self.revision += 1
        self.notify(linePreviewUrl=self.url('line-preview.png'))

    @Slot()
    def acceptLines(self):
        if self.data['busy'] or not self.line_preview:
            return
        self.record_undo(None)
        sheet = self.document.active_sheet
        self.document.lines = [f for f in self.document.lines if f['properties'].get('source_sheet') != sheet
                               or f['properties'].get('review') in ('edited', 'accepted')]
        next_id = max((f['properties']['object_id'] for f in self.document.lines), default=0)+1
        for number, feature in enumerate(self.line_preview):
            props = dict(feature['properties'], **defaults(self.document.line_fields), object_id=next_id+number, source_sheet=sheet)
            self.document.lines.append(dict(geometry=feature['geometry'], properties=props))
        self.line_preview = []
        self.notify(linePreviewCount=0, linePreviewUrl='')
        self.line_changed()

    @Slot()
    def discardLines(self):
        self.line_preview = []
        self.notify(linePreviewCount=0, linePreviewUrl='')

    def line_changed(self):
        if self.result is not None:
            preview_overlay(self.document, self.result, Path(self.temp.name)/'overlay.png', Path(self.temp.name)/'lines.png')
        self.revision += 1
        self.mark_dirty()
        self.publish_document()
        self.publish_line_selection()
        self.notify(linesUrl=self.url('lines.png') if self.document.lines else '', error=False,
                    webMapRevision=self.data.get('webMapRevision',0)+1)

    def world(self, x, y):
        return self.raster.transform*(x, y)

    def line_mutable(self):
        return self.raster is not None and not self.data['busy'] and not self.data['lineLocked']

    def line_geometry_allowed(self,geometry,originals=()):
        from .region import footprint
        from shapely.ops import unary_union
        outside=geometry.difference(footprint(self.raster))
        if originals and not outside.is_empty:
            outside=outside.difference(unary_union(originals).buffer(abs(self.raster.transform.a)*1e-7))
        return outside.length<=max(1e-8,geometry.length*1e-9)

    @Slot(float, float, float, bool)
    def lineClick(self, x, y, tolerance, additive=False):
        if self.raster is None or self.data['busy']:
            return
        p = self.world(x, y)
        if self.document.area is not None and not self.document.area.covers(Point(p)):
            return
        tool = self.data['lineTool']
        if tool == 'draw' and self.line_mutable():
            self.line_points.append(p)
            inverse = ~self.raster.transform
            self.notify(lineDraft=[dict(x=(inverse*q)[0], y=(inverse*q)[1]) for q in self.line_points])
            return
        distance = tolerance*abs(self.raster.transform.a)
        nearby = [(f['geometry'].distance(Point(p)), f) for f in self.document.lines]
        nearby = sorted((row for row in nearby if row[0] <= distance), key=lambda row: row[0])
        if not nearby:
            self.line_selection = []
        else:
            feature = nearby[0][1]
            identity = feature['properties']['object_id']
            if tool in ('insert', 'remove_vertex') and self.line_mutable():
                try:
                    geometry = edit_vertex(feature['geometry'], p, remove=tool == 'remove_vertex')
                    if not self.line_geometry_allowed(geometry,[feature['geometry']]):
                        raise ValueError('Правка изменяет линию вне доступного участка.')
                    self.record_undo(None)
                    feature['geometry'] = geometry
                    feature['properties']['review'] = 'edited'
                    self.line_selection = [identity]
                    self.line_changed()
                except ValueError as exc:
                    self.notify(error=True, status=str(exc))
                return
            if tool == 'split' and self.line_mutable():
                try:
                    parts = split_line(feature['geometry'], p)
                    self.record_undo(None)
                    feature['geometry'] = parts[0]
                    props = dict(feature['properties'], object_id=max(f['properties']['object_id'] for f in self.document.lines)+1, review='edited')
                    feature['properties']['review'] = 'edited'
                    self.document.lines.append(dict(geometry=parts[1], properties=props))
                    self.line_changed()
                except ValueError as exc:
                    self.notify(error=True, status=str(exc))
                return
            if additive:
                self.line_selection = [i for i in self.line_selection if i != identity] if identity in self.line_selection else self.line_selection+[identity]
            else:
                self.line_selection = [identity]
        self.publish_line_selection()

    def publish_line_selection(self):
        chosen = [f for f in self.document.clipped_lines() if f['properties']['object_id'] in self.line_selection]
        value = dict(chosen[0]['properties']) if chosen else {}
        if chosen:
            value['length_m'] = sum(measurements(f['geometry'])['length_m'] for f in chosen)
        self.notify(lineSelected=list(dict.fromkeys(f['properties']['object_id'] for f in chosen)), lineSelection=value)
        if self.raster is None:
            return
        from PIL import ImageDraw
        image = Image.new('RGBA', (self.data['width'], self.data['height']))
        draw = ImageDraw.Draw(image)
        inverse = ~self.raster.transform
        paths=[]
        for f in chosen:
            points = [inverse*p for p in f['geometry'].coords]
            paths.append([dict(x=p[0],y=p[1]) for p in points])
            draw.line(points, fill='white', width=5)
            draw.line(points, fill='#356ac3', width=2)
        image.save(Path(self.temp.name)/'line-selection.png')
        self.revision += 1
        self.notify(lineSelectionUrl=self.url('line-selection.png') if chosen else '',lineSelectionPaths=paths)

    @Slot()
    def finishLine(self):
        if not self.line_mutable() or len(self.line_points) < 2:
            return
        try:
            line = draw_line(self.line_points)
            from .region import footprint
            if line.difference(footprint(self.raster)).length > line.length*1e-9:
                raise ValueError('Линия выходит за доступную карту или участок.')
            type_id = self.data.get('currentLineType')
            if type_id is None:
                raise ValueError('Выберите тип линии.')
            self.record_undo(None)
            identity = max((f['properties']['object_id'] for f in self.document.lines), default=0)+1
            props = dict(defaults(self.document.line_fields), object_id=identity, type_id=type_id, name='', note='', review='edited', origin='manual')
            if self.line_continue is not None:
                old = next(f for f in self.document.lines if f['properties']['object_id'] == self.line_continue)
                coords = list(old['geometry'].coords)
                if math.dist(coords[0], self.line_points[0]) < math.dist(coords[-1], self.line_points[0]):
                    coords.reverse()
                old['geometry'] = draw_line(coords+self.line_points[1:])
                old['properties']['review'] = 'edited'
                identity = self.line_continue
                self.line_continue = None
            else:
                self.document.lines.append(dict(geometry=line, properties=props))
            self.line_points, self.line_selection = [], [identity]
            self.notify(lineDraft=[])
            self.line_changed()
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot()
    def clearLineDraft(self):
        self.line_points = []
        self.line_selection = []
        self.line_continue = None
        self.line_drag = None
        self.notify(lineDraft=[], lineSelected=[], lineSelection={}, lineSelectionUrl='', lineDragPreview=[],lineSelectionPaths=[])

    @Slot()
    def continueLine(self):
        if not self.line_mutable() or len(self.line_selection) != 1:
            return
        feature = next(f for f in self.document.lines if f['properties']['object_id'] == self.line_selection[0])
        from .region import footprint
        domain=footprint(self.raster)
        ends=[tuple(feature['geometry'].coords[-1]),tuple(feature['geometry'].coords[0])]
        end=next((p for p in ends if domain.covers(Point(p))),None)
        if end is None:
            self.notify(error=True,status='Оба конца линии за пределами участка. Расширьте участок или разделите линию в нужном месте.')
            return
        self.line_continue = self.line_selection[0]
        self.line_points = [end]
        p = (~self.raster.transform)*self.line_points[0]
        self.notify(lineTool='draw', lineDraft=[dict(x=p[0], y=p[1])], currentLineType=feature['properties']['type_id'],
                    status='Продолжение с конца выбранной линии. Щёлкайте точки; Enter — завершить, Escape — отменить.')

    @Slot()
    def reverseContinuation(self):
        if self.line_continue is None:
            return
        feature = next(f for f in self.document.lines if f['properties']['object_id'] == self.line_continue)
        end = tuple(feature['geometry'].coords[0])
        if self.line_points and self.line_points[0] == end:
            end = tuple(feature['geometry'].coords[-1])
        from .region import footprint
        if not footprint(self.raster).covers(Point(end)):
            self.notify(error=True,status='Другой конец находится за пределами участка.')
            return
        self.line_points = [end]
        p = (~self.raster.transform)*end
        self.notify(lineDraft=[dict(x=p[0], y=p[1])])

    @Slot()
    def removeLinePoint(self):
        if self.line_points:
            self.line_points.pop()
            inverse = ~self.raster.transform
            self.notify(lineDraft=[dict(x=(inverse*p)[0], y=(inverse*p)[1]) for p in self.line_points])

    @Slot(float, float, float, result=bool)
    def beginLineDrag(self, x, y, tolerance):
        if not self.line_mutable():
            return False
        p = self.world(x, y)
        near = sorted([(f['geometry'].distance(Point(p)), f) for f in self.document.lines], key=lambda row: row[0])
        if not near or near[0][0] > tolerance*abs(self.raster.transform.a):
            return False
        self.line_drag = (near[0][1]['properties']['object_id'], p, tolerance*8*abs(self.raster.transform.a))
        self.line_selection = [self.line_drag[0]]
        self.publish_line_selection()
        self.previewLineDrag(0, 0)
        return True

    @Slot(float, float)
    def previewLineDrag(self, dx, dy):
        if self.line_drag is None or self.raster is None:
            return
        identity, p, radius = self.line_drag
        feature = next(f for f in self.document.lines if f['properties']['object_id'] == identity)
        t = self.raster.transform
        geometry = drag_line(feature['geometry'], p, t.a*dx+t.b*dy, t.d*dx+t.e*dy, radius, self.data['lineTool'] == 'vertex')
        inverse = ~t
        coords = list(geometry.coords)
        if len(coords) > 1500:
            coords = list(geometry.simplify(abs(t.a)*.25).coords)
        self.notify(lineDragPreview=[dict(x=(inverse*q)[0], y=(inverse*q)[1]) for q in coords])

    @Slot(float, float)
    def finishLineDrag(self, dx, dy):
        if self.line_drag is None or not self.line_mutable():
            return
        identity, p, radius = self.line_drag
        self.line_drag = None
        self.notify(lineDragPreview=[])
        feature = next(f for f in self.document.lines if f['properties']['object_id'] == identity)
        try:
            t = self.raster.transform
            geometry = drag_line(feature['geometry'], p, t.a*dx+t.b*dy, t.d*dx+t.e*dy, radius, self.data['lineTool'] == 'vertex')
            if not self.line_geometry_allowed(geometry,[feature['geometry']]):
                raise ValueError('Изменение выводит линию за участок.')
            self.record_undo(None)
            feature['geometry'] = geometry
            feature['properties']['review'] = 'edited'
            self.line_changed()
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot()
    def cancelLineDrag(self):
        self.line_drag=None
        self.notify(lineDragPreview=[])

    @Slot()
    def deleteSelectedLines(self):
        if self.line_mutable() and self.line_selection:
            self.record_undo(None)
            self.document.lines = [f for f in self.document.lines if f['properties']['object_id'] not in self.line_selection]
            self.line_selection = []
            self.line_changed()

    @Slot()
    def joinSelectedLines(self):
        if not self.line_mutable() or len(self.line_selection) < 2:
            return
        chosen = [f for f in self.document.lines if f['properties']['object_id'] in self.line_selection]
        if len({f['properties']['type_id'] for f in chosen}) != 1:
            self.notify(error=True, status='Соединяйте линии одного типа.')
            return
        try:
            geometry = join_lines([f['geometry'] for f in chosen])
            if not self.line_geometry_allowed(geometry,[f['geometry'] for f in chosen]):
                raise ValueError('Соединение выходит за доступную карту или участок.')
            self.record_undo(None)
            props = dict(chosen[0]['properties'], review='edited')
            self.document.lines = [f for f in self.document.lines if f['properties']['object_id'] not in self.line_selection]
            self.document.lines.append(dict(geometry=geometry, properties=props))
            self.line_selection = [props['object_id']]
            self.line_changed()
        except ValueError as exc:
            self.notify(error=True, status=str(exc))

    @Slot(str, result=bool)
    def applyLineAttributes(self, encoded):
        if not self.line_mutable() or not self.line_selection:
            return False
        try:
            values = json.loads(encoded)
            if not isinstance(values, dict) or set(values)-({'name', 'note', 'type_id'} | {f['key'] for f in self.document.line_fields}):
                raise ValueError('Неизвестные или вычисляемые поля линии.')
            if any(k in values and (not isinstance(values[k], str) or len(values[k]) > 10000) for k in ('name', 'note')):
                raise ValueError('Некорректное название или примечание.')
            if 'type_id' in values and values['type_id'] not in {t['id'] for t in self.document.line_types}:
                raise ValueError('Тип линии не найден.')
            updates = []
            for f in self.document.lines:
                if f['properties']['object_id'] not in self.line_selection:
                    continue
                props = dict(f['properties'], **values)
                props.update(values_for(self.document.line_fields, props), review='edited')
                updates.append((f, props))
            self.record_undo(None)
            for feature, props in updates:
                feature['properties'] = props
            self.line_changed()
            return True
        except (ValueError, TypeError) as exc:
            self.notify(error=True, status=str(exc))
            return False

    @Slot(str, str, result=bool)
    def saveLineField(self, encoded, original):
        if self.data['busy'] or self.data.get('lineLocked'):
            return False
        try:
            definition = json.loads(encoded)
            fields = [f for f in self.document.line_fields if f['key'] != original]+[definition]
            fields = validate_schema(fields)
            forbidden={'type_id','type_name','name','length_m','area_m2','area_ha','source_sheet','source_group','class_name','perimeter_m'}
            if any(f['key'] in forbidden for f in fields):
                raise ValueError('Этот код зарезервирован для свойств объекта.')
            updates = [values_for(fields, f['properties']) for f in self.document.lines]
            self.record_undo(None)
            self.document.line_fields = fields
            for f, values in zip(self.document.lines, updates):
                for old in list(f['properties']):
                    if old == original and original not in {f['key'] for f in fields}:
                        f['properties'].pop(old)
                f['properties'].update(values)
            self.line_changed()
            return True
        except (ValueError, TypeError, KeyError) as exc:
            self.notify(error=True, status=str(exc))
            return False

    @Slot(result='QVariantMap')
    def defaultLayout(self):
        settings=layout_defaults(self.document)
        if self.result is not None:
            present={f['properties']['class_id'] for f in self.result.features}
            settings['class_ids']=[c['id'] for c in self.document.classes if c['id'] in present]
        return settings

    @Slot(str)
    def previewLayout(self, encoded):
        if self.data['busy'] or self.result is None:
            return
        try:
            self.layout_settings = validate_layout(json.loads(encoded), self.document)
            self.task('layout', layout_preview, self.document.clone(), self.result, self.layout_settings, self.temp.name)
        except (ValueError, TypeError) as exc:
            self.notify(error=True, status=str(exc))

    @Slot(str, result=bool)
    def saveLayoutTemplate(self, encoded):
        try:
            settings = validate_layout(json.loads(encoded), self.document)
            self.record_undo(None)
            self.document.layouts = [s for s in self.document.layouts if s['name'] != settings['name']]+[settings]
            self.mark_dirty()
            self.publish_document()
            return True
        except (ValueError, TypeError) as exc:
            self.notify(error=True, status=str(exc))
            return False

    @Slot(int, result='QVariantMap')
    def loadLayoutTemplate(self, index):
        if 0 <= index < len(self.document.layouts):
            return copy.deepcopy(self.document.layouts[index])
        return layout_defaults(self.document)

    @Slot(QUrl, str)
    def exportLayout(self, url, encoded):
        if self.data['busy'] or self.result is None:
            return
        try:
            settings = validate_layout(json.loads(encoded), self.document)
            path = url.toLocalFile()
            if not Path(path).suffix:
                path += '.pdf'
            self.task('export_layout', export_layout, self.document.clone(), self.result, settings, path)
        except (ValueError, TypeError) as exc:
            self.notify(error=True, status=str(exc))

    @Slot(QUrl, str)
    def exportAtlas(self, url, encoded):
        if self.data['busy'] or self.result is None:
            return
        try:
            settings = validate_layout(json.loads(encoded), self.document)
            path = url.toLocalFile()
            if not Path(path).suffix:
                path += '.pdf'
            self.task('export_layout', export_layout, self.document.clone(), self.result, settings, path, True)
        except (ValueError, TypeError) as exc:
            self.notify(error=True, status=str(exc))

    @Slot()
    def checkSeams(self):
        if not self.data['busy'] and self.result is not None:
            self.task('seams', seam_sites, self.document.clone(), self.result)

    @Slot(float, float, float, float)
    def rememberMapView(self, x, y, width, height):
        if self.raster is not None:
            self.last_view = [*self.world(x, y+height), *self.world(x+width, y)]

    @Slot(float, float, float, float, int, int)
    def requestDetail(self, x, y, width, height, pixels_w, pixels_h):
        if self.raster is None or width <= 0 or height <= 0:
            return
        bounds = [*self.world(x, y+height), *self.world(x+width, y)]
        self.detail_tiles.request(bounds,pixels_w,pixels_h)
        self.publish_detail_tiles()
        ratio = min(1., 2400/max(1, pixels_w, pixels_h), math.sqrt(4_000_000/max(1, pixels_w*pixels_h)))
        w, h = max(1, round(pixels_w*ratio)), max(1, round(pixels_h*ratio))
        key = (tuple(bounds), w, h, self.edit_revision, id(self.raster))
        if key == self.detail_key:
            return
        self.detail_key = key
        self.detail_pending = (key, self.document.clone(), _copy_result(self.result), bounds, w, h, dict(x=x,y=y,w=width,h=height))
        self.poll_detail()

    def publish_detail_tiles(self):
        rows=self.detail_tiles.rows()
        if rows!=self.data.get('detailTiles',[]):
            values=dict(detailTiles=rows)
            if rows:values.update(detailUrl=rows[-1]['url'],detailRect={k:rows[-1][k] for k in ('x','y','w','h')})
            self.notify(**values)

    def poll_detail(self):
        if hasattr(self,'detail_tiles') and self.detail_tiles.pump():self.publish_detail_tiles()
        if self.detail_future is not None and self.detail_future.done():
            future, self.detail_future = self.detail_future, None
            try:
                output = future.result()
                if self.detail_running is not None and self.detail_running[0] == self.detail_key:
                    rect = self.detail_running[-1]
                    inverse=~self.raster.transform
                    for item in output.get('detailLabels',[]):
                        item['x'],item['y']=inverse*(item['x'],item['y'])
                    self.notify(**output, detailRect=rect)
            except Exception as exc:
                # The low-resolution working image remains usable on read failure.
                self.detail_error=f'{type(exc).__name__}: {exc}'
            self.detail_running = None
        if self.detail_future is None and self.detail_pending is not None:
            item, self.detail_pending = self.detail_pending, None
            self.detail_running = item
            _, doc, result, bounds, w, h, _ = item
            self.detail_future = self.detail_pool.submit(viewport_images, doc, result, bounds, w, h,True)

    def close_details(self):
        self.setCompareShortcutEnabled(False)
        self.detail_pool.shutdown(wait=True, cancel_futures=True)
        self.update_cancel.set()
        self.update_pool.shutdown(wait=False,cancel_futures=True)

    @Slot()
    def checkUpdates(self):
        if self.update_future is not None:return
        from .updates import check_updates
        from . import __version__
        self.update_release=None;self.update_kind='check'
        self.notify(updateState='checking',updateMessage='Проверяю выпуски…',updateProgress=0,updateNotes='')
        self.update_future=self.update_pool.submit(check_updates,__version__)

    @Slot()
    def downloadUpdate(self):
        if self.update_future is not None or not self.update_release:return
        from .updates import download_update
        from PySide6.QtCore import QStandardPaths
        self.update_cancel.clear();self.update_progress=(0,self.update_release['size']);self.update_kind='download'
        folder=Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation))/'updates'
        self.notify(updateState='downloading',updateMessage='Загружаю установщик…',updateProgress=0)
        def progress(done,total):self.update_progress=(done,total)
        self.update_future=self.update_pool.submit(download_update,self.update_release,folder,progress,self.update_cancel)

    @Slot()
    def cancelUpdateDownload(self):
        self.update_cancel.set()

    def poll_updates(self):
        future=self.update_future
        if future is None:return
        if self.data.get('updateState')=='downloading':
            done,total=self.update_progress;percent=round(done/max(1,total)*100)
            if percent!=self.data.get('updateProgress'):self.notify(updateProgress=percent)
        if not future.done():return
        self.update_future=None
        try:
            result=future.result()
            if self.update_kind=='check':
                self.update_release=result if result['state']=='available' else None
                self.notify(updateState=result['state'],updateMessage=result['message'],updateVersion=result.get('version',''),updateNotes=result.get('notes',''))
            else:
                self.update_release=result
                self.notify(updateState='ready',updateProgress=100,updateMessage='Установщик загружен и проверен. Перед обновлением сохраните работу.')
        except Exception as exc:
            from .updates import DownloadCancelled
            self.notify(updateState='available' if isinstance(exc,DownloadCancelled) and self.update_release else 'error',updateMessage=str(exc))

    @Slot(result=bool)
    def launchUpdate(self):
        if self.data['busy'] or self.data.get('updateState')!='ready':return False
        try:
            from .updates import launch_installer
            launch_installer(self.update_release)
            return True
        except Exception as exc:
            self.notify(updateState='error',updateMessage=str(exc));return False

    @Slot()
    def openReleasePage(self):
        from .updates import RELEASES
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(RELEASES))

    @Slot(str, result=bool)
    def removeLineField(self, key):
        if self.data['busy'] or self.data.get('lineLocked'):
            return False
        if not any(f['key'] == key for f in self.document.line_fields):
            return False
        self.record_undo(None)
        self.document.line_fields = [f for f in self.document.line_fields if f['key'] != key]
        for f in self.document.lines:
            f['properties'].pop(key, None)
        self.line_changed()
        return True

    @Slot(result='QVariantList')
    def currentMapBounds(self):
        return self.last_view or (list(self.raster.area.bounds) if self.raster is not None else [])

    @Slot(float, float, float, float)
    def setExportFrame(self, x0, y0, x1, y1):
        if self.raster is None or x1-x0 < 2 or y1-y0 < 2:
            self.notify(error=True, status='Выделите прямоугольную область карты.')
            return
        self.notify(exportFrameBounds=[*self.world(x0, y1), *self.world(x1, y0)])
        self.exportFrameReady.emit()

    def start(self, kind, function, *args):
        if self.data.get('polygonsLocked') and kind in ('geometry', 'drag', 'merge_direct', 'apply', 'segment', 'auto_preview'):
            self.notify(error=True, status='Слой полигонов заблокирован. Разблокируйте его в слоях.')
            return
        from .app import edit_job
        if function is edit_job:
            args=tuple(args)+(None,)*(7-len(args))+(True,)
        super().start(kind, function, *args)

    def polygon_locked(self):
        if self.data.get('polygonsLocked'):
            self.notify(error=True, status='Слой полигонов заблокирован. Разблокируйте его в слоях.')
            return True
        return False

    @Slot()
    def refreshWebMapContext(self):
        try:
            self.web_map.set_project(self.raster,self.result,self.document,self.edit_revision)
        except (ValueError,OSError):
            self.web_map.set_project(None,None)

    @Slot()
    def applyBulk(self):
        if not self.polygon_locked():
            super().applyBulk()

    @Slot(str, str, bool, int, result=bool)
    def applyAttributeScope(self, encoded, patch, only_empty, epoch):
        return False if self.polygon_locked() else super().applyAttributeScope(encoded, patch, only_empty, epoch)

    @Slot(str, str)
    def saveAttributes(self, note, custom_json):
        if not self.polygon_locked():
            super().saveAttributes(note, custom_json)

    @Slot(str, str, result=bool)
    def saveField(self, definition, original_key):
        if self.polygon_locked():
            return False
        try:
            key=json.loads(definition)['key']
            if key in {'area_m2','area_ha','perimeter_m','length_m','source_sheet','source_group','class_name','type_id','type_name'}:
                raise ValueError('Этот код зарезервирован для вычисляемых свойств объекта.')
        except (ValueError, TypeError, KeyError) as exc:
            self.notify(error=True, status=str(exc), schemaError=str(exc))
            return False
        return super().saveField(definition, original_key)

    @Slot(str)
    def removeField(self, key):
        if not self.polygon_locked():
            super().removeField(key)

    @Slot()
    def openManual(self):
        from PySide6.QtGui import QDesktopServices
        local=Path(__file__).parent/'resources/docs/USER_GUIDE.html'
        if local.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(local)))
        else:
            path=Path(__file__).resolve().parents[2]/'docs/USER_GUIDE.md'
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
