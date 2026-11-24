"""A licence-area project: immutable scan sheets, common classes and editable output.

Recognition remains in each scan's original pixel grid. The shared display and
output layer use EPSG:3857; KML clipping never destroys the source sheet.
"""
from dataclasses import dataclass, field, replace
from io import BytesIO
import copy
import json
import os
import re
from pathlib import Path
import tempfile
import uuid
import zipfile

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

from .attributes import validate_schema, values_for
from .automatic import protected_feature
from .core import Raster, Result, read_raster
from .project import save_project, load_project, VIEW_DEFAULTS, validate_view
from .region import footprint, polygon_parts
from .spatial import DISPLAY_CRS, project_geometry, measurements
from .styles import validate_style, DEFAULT_POLYGON

DOCUMENT_FORMAT = 'geotrace-workspace'
DOCUMENT_VERSION = 1
MAX_SHEETS = 64
MAX_PROJECT_BYTES = 1024 * 1024 * 1024


@dataclass
class Sheet:
    id: str
    name: str
    raster: Raster
    result: Result | None = None
    groups: dict = field(default_factory=dict)
    visible: bool = True
    locked: bool = True
    recipe: dict = field(default_factory=dict)

    @classmethod
    def read(cls, path):
        return cls(uuid.uuid4().hex, Path(path).stem, read_raster(path))

    def footprint(self):
        return project_geometry(footprint(self.raster), self.raster.crs_wkt, densify=True)


@dataclass
class Document:
    sheets: list = field(default_factory=list)
    active_sheet: str = ''
    classes: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    line_fields: list = field(default_factory=list)
    line_types: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    areas: list = field(default_factory=list)
    active_area: int = -1
    manual: Result | None = None
    view: dict = field(default_factory=lambda: dict(VIEW_DEFAULTS))
    layouts: list = field(default_factory=list)
    identities: dict = field(default_factory=dict)
    dissolve_classes: bool = False
    source_project: str = ''
    settings: dict = field(default_factory=dict)

    def clone(self):
        output = copy.copy(self)
        output.sheets = [replace(s, groups=dict(s.groups), recipe=copy.deepcopy(s.recipe), result=_copy_result(s.result)) for s in self.sheets]
        for name in ('classes', 'fields', 'line_fields', 'line_types', 'lines', 'areas', 'view', 'layouts', 'identities', 'settings'):
            setattr(output, name, copy.deepcopy(getattr(self, name)))
        output.manual = _copy_result(self.manual)
        return output

    def sheet(self, identity=None):
        identity = identity or self.active_sheet
        return next((s for s in self.sheets if s.id == identity), None)

    @property
    def area(self):
        return self.areas[self.active_area]['geometry'] if 0 <= self.active_area < len(self.areas) else None

    def add_paths(self, paths):
        if len(paths)+len(self.sheets) > MAX_SHEETS:
            raise ValueError(f'В одном проекте допускается до {MAX_SHEETS} листов.')
        added = []
        known = {str(Path(s.raster.path).resolve()) for s in self.sheets}
        # Read the entire batch before changing the document.
        for path in paths:
            full = str(Path(path).resolve())
            if full in known:
                continue
            added.append(Sheet.read(full))
            known.add(full)
        self.sheets.extend(added)
        if added:
            self.active_sheet = added[0].id
        return added

    def set_sheet_result(self, identity, result):
        sheet = self.sheet(identity)
        if sheet is None:
            raise ValueError('Лист не найден.')
        sheet.result = result
        for local, rgb in enumerate(result.colors, 1):
            if local not in sheet.groups:
                cid = len(self.classes)+1
                color = '#%02x%02x%02x' % tuple(rgb)
                self.classes.append(dict(id=cid, name=f'{sheet.name} · группа {local}'[:200],
                                         style=validate_style(dict(fill=color))))
                sheet.groups[local] = cid

    def assign_group(self, sheet_id, local, class_id, name=''):
        if not isinstance(name,str) or len(name)>200:
            raise ValueError('Название класса должно содержать не более 200 символов.')
        sheet = self.sheet(sheet_id)
        if sheet is None or sheet.result is None or not 1 <= local <= len(sheet.result.colors):
            raise ValueError('Неизвестная группа листа.')
        if class_id == 0:
            class_id = len(self.classes)+1
            rgb = sheet.result.colors[local-1]
            self.classes.append(dict(id=class_id, name=name.strip() or f'Класс {class_id}',
                                     style=validate_style(dict(fill='#%02x%02x%02x' % tuple(rgb)))))
        if not any(c['id'] == class_id for c in self.classes):
            raise ValueError('Класс проекта не найден.')
        sheet.groups[local] = class_id

    def _identity(self, key):
        if key not in self.identities:
            self.identities[key] = max(self.identities.values(), default=0)+1
        return self.identities[key]

    def canvas(self, side=1600):
        if not self.sheets:
            raise ValueError('Добавьте хотя бы один лист карты.')
        domain = unary_union([s.footprint() for s in self.sheets])
        bounds = domain.bounds
        x0, y0, x1, y1 = bounds
        if x1 <= x0 or y1 <= y0 or max(abs(v) for v in bounds) > 25_000_000:
            raise ValueError('Недопустимый охват листов для EPSG:3857.')
        scale = side/max(x1-x0, y1-y0)
        w, h = max(1, round((x1-x0)*scale)), max(1, round((y1-y0)*scale))
        affine = from_bounds(*bounds, w, h)
        rgb = np.full((h, w, 3), 245, np.uint8)
        valid = np.zeros((h, w), bool)
        for sheet in self.sheets:
            source = sheet.raster
            mask = np.zeros((h, w), np.uint8)
            reproject(source.valid.astype(np.uint8), mask, src_transform=source.transform,
                      src_crs=source.crs_wkt, dst_transform=affine, dst_crs=DISPLAY_CRS,
                      resampling=Resampling.nearest, num_threads=1)
            valid |= mask > 0
            if not sheet.visible:
                continue
            for channel in range(3):
                pixels = np.zeros((h, w), np.uint8)
                reproject(source.rgb[:, :, channel], pixels, src_transform=source.transform,
                          src_crs=source.crs_wkt, dst_transform=affine, dst_crs=DISPLAY_CRS,
                          resampling=Resampling.bilinear, num_threads=1)
                rgb[:, :, channel][mask > 0] = pixels[mask > 0]
        raster = Raster('', rgb, valid, affine, CRS.from_string(DISPLAY_CRS).to_wkt(), DISPLAY_CRS, (w, h))
        # The overview grid is only a display cache. Keep the exact transformed
        # footprint for edits; centre-sampled pixels alone lose thin edge belts.
        raster.valid = rasterize([(domain, 1)], out_shape=(h, w), transform=affine,
                                 all_touched=True, dtype='uint8').astype(bool)
        raster.area, raster.source_valid = domain, raster.valid
        if self.area is not None:
            from .region import set_area
            raster = set_area(raster, self.area.intersection(domain), self.areas[self.active_area]['name'])
        return raster

    def compose(self, canvas=None):
        canvas = canvas or self.canvas()
        features = []
        occupied = None
        # Higher sheets in the list have priority in overlap. Preserve all sources.
        for sheet in reversed(self.sheets):
            if sheet.result is None:
                continue
            current = []
            frame = sheet.footprint()
            for feature in sheet.result.features:
                geometry = project_geometry(feature['geometry'], sheet.raster.crs_wkt, densify=True)
                if sheet.raster.area is not None:
                    geometry = geometry.intersection(frame)
                if self.area is not None:
                    geometry = geometry.intersection(self.area)
                if occupied is not None:
                    geometry = geometry.difference(occupied)
                for number, part in enumerate(polygon_parts(geometry)):
                    if not part.is_valid:
                        raise ValueError('Некорректная геометрия на стыке листов.')
                    props = dict(feature['properties'])
                    local = props['class_id']
                    props.update(class_id=sheet.groups[local], source_sheet=sheet.id,
                                 source_group=local,
                                 object_id=self._identity(f"{sheet.id}:{props['object_id']}:{number}"))
                    current.append(dict(geometry=part, properties=props))
            if current:
                union = unary_union([f['geometry'] for f in current])
                occupied = union if occupied is None else occupied.union(union)
                features.extend(current)
        if self.dissolve_classes:
            merged = []
            for cid in sorted({f['properties']['class_id'] for f in features}):
                members = [f for f in features if f['properties']['class_id'] == cid]
                for number, geometry in enumerate(polygon_parts(unary_union([f['geometry'] for f in members]))):
                    props = dict(members[0]['properties'], source_sheet=';'.join(sorted({f['properties']['source_sheet'] for f in members})),
                                 object_id=self._identity(f'class:{cid}:{number}'))
                    merged.append(dict(geometry=geometry, properties=props))
            features = merged
        palette = [list(bytes.fromhex(c['style']['fill'][1:])) for c in self.classes]
        labels = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                           out_shape=canvas.valid.shape, transform=canvas.transform, dtype='uint16') if features else np.zeros(canvas.valid.shape, np.uint16)
        labels[~canvas.valid] = 0
        result = Result(canvas, labels, features, palette, self.fields)
        if self.manual is not None:
            result = self.preserve_manual(result)
        return result

    def preserve_manual(self, generated):
        domain = unary_union([s.footprint() for s in self.sheets])
        if self.area is not None:
            domain = domain.intersection(self.area)
        kept = []
        for feature in self.manual.features:
            oid = feature['properties']['object_id']
            if oid not in self.identities.values():
                self.identities[f'manual-object:{oid}'] = oid
        for feature in self.manual.features:
            if not protected_feature(feature, self.manual.fields):
                continue
            geometry = feature['geometry']
            if not domain.covers(geometry) and geometry.difference(domain).area > geometry.area*1e-12:
                geometry = geometry.intersection(domain)
            for number, part in enumerate(polygon_parts(geometry)):
                props = dict(feature['properties'])
                if number:
                    props['object_id'] = self._identity(f"manual:{props['object_id']}:{number}")
                kept.append(dict(geometry=part, properties=props))
        if not kept:
            return generated
        occupied = unary_union([f['geometry'] for f in kept])
        used = {f['properties']['object_id'] for f in kept}
        features = list(kept)
        for feature in generated.features:
            for number, part in enumerate(polygon_parts(feature['geometry'].difference(occupied))):
                props = dict(feature['properties'])
                if number or props['object_id'] in used:
                    props['object_id'] = self._identity(f"remainder:{props['object_id']}:{number}")
                features.append(dict(geometry=part, properties=props))
        labels = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                           out_shape=generated.labels.shape, transform=generated.raster.transform, dtype='uint16')
        labels[~generated.raster.valid] = 0
        return Result(generated.raster, labels, features, generated.colors, self.fields)

    def clipped_lines(self):
        output = []
        for feature in self.lines:
            geometry = feature['geometry']
            if self.area is not None:
                geometry = geometry.intersection(self.area)
            for line in line_parts(geometry):
                output.append(dict(geometry=line, properties=dict(feature['properties'])))
        return output

    def summary(self, result):
        rows = []
        total = 0.
        for cls in self.classes:
            members = [f for f in result.features if f['properties']['class_id'] == cls['id']]
            area = sum(measurements(f['geometry'])['area_m2'] for f in members)
            total += area
            rows.append(dict(id=cls['id'], name=cls['name'], count=len(members), area_m2=area))
        site_area = measurements(self.area)['area_m2'] if self.area is not None else total
        for row in rows:
            row['share'] = row['area_m2']/site_area if site_area else 0.
        clipped = self.clipped_lines()
        return dict(classes=rows, area_m2=total,
                    site_area_m2=site_area, uncovered_m2=max(0.,site_area-total),
                    length_m=sum(measurements(f['geometry'])['length_m'] for f in clipped),
                    polygons=len(result.features), lines=len({f['properties']['object_id'] for f in clipped}))


def line_parts(geometry):
    if geometry.geom_type == 'LineString' and not geometry.is_empty and geometry.length > 0:
        yield geometry
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from line_parts(part)


def _copy_result(result):
    if result is None:
        return None
    return Result(result.raster, result.labels, [dict(geometry=f['geometry'], properties=copy.deepcopy(f['properties']))
                  for f in result.features], copy.deepcopy(result.colors), copy.deepcopy(result.fields))


def _records(features):
    return [dict(geometry=mapping(f['geometry']), properties=f['properties']) for f in features]


def save_document(path, document, canvas=None, result=None, portable=False):
    path = Path(path)
    if path.suffix.lower() != '.geotrace':
        raise ValueError('Проект должен иметь расширение .geotrace.')
    if not document.sheets:
        raise ValueError('Проект не содержит карт.')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', suffix='.tmp', dir=path.parent)
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory(prefix='geotrace-snapshot-') as work:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                sheets = []
                for index, sheet in enumerate(document.sheets):
                    snapshot = Path(work)/f'{index}.geotrace'
                    save_project(snapshot, sheet.raster, sheet.result)
                    archive.write(snapshot, f'sheets/{index}.geotrace')
                    record = dict(id=sheet.id, name=sheet.name, groups=sheet.groups,
                                  visible=sheet.visible, locked=sheet.locked, recipe=sheet.recipe)
                    if (portable or document.settings.get('portable_sources')) and Path(sheet.raster.path).is_file():
                        name = f'sources/{index}.tif'
                        archive.write(sheet.raster.path, name)
                        record['embedded_source'] = name
                        for suffix in ('.msk', '.aux.xml'):
                            sidecar = Path(sheet.raster.path+suffix)
                            if sidecar.is_file():
                                archive.write(sidecar, name+suffix)
                    sheets.append(record)
                if result is not None:
                    snapshot = Path(work)/'output.geotrace'
                    save_project(snapshot, canvas or result.raster, result)
                    archive.write(snapshot, 'output.geotrace')
                meta = dict(format=DOCUMENT_FORMAT, version=DOCUMENT_VERSION,
                            sheets=sheets, active_sheet=document.active_sheet, classes=document.classes,
                            fields=document.fields, line_fields=document.line_fields, line_types=document.line_types,
                            lines=_records(document.lines), areas=[dict(name=a['name'], geometry=mapping(a['geometry'])) for a in document.areas],
                            active_area=document.active_area, view=document.view, layouts=document.layouts,
                            identities=document.identities, dissolve_classes=document.dissolve_classes,
                            source_project=document.source_project)
                meta['settings'] = document.settings
                archive.writestr('workspace.json', json.dumps(meta, ensure_ascii=False, allow_nan=False))
        with open(temporary, 'rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(path)


def load_document(path, source_cache=None):
    """Read JSON/NPY only. No untrusted names are ever extracted as paths."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if 'workspace.json' not in names:
                old = load_project(path)
                document = Document(fields=old.result.fields if old.result else [], view=old.view)
                raster = old.raster
                if raster.source_valid is not None:
                    raster = replace(raster, valid=raster.source_valid, source_valid=None, area=None, area_name='')
                sheet = Sheet(uuid.uuid4().hex, Path(raster.path).stem, raster)
                document.sheets = [sheet]
                document.active_sheet = sheet.id
                if old.result is not None:
                    document.set_sheet_result(sheet.id, old.result)
                if old.raster.area is not None:
                    document.areas = [dict(name=old.raster.area_name, geometry=project_geometry(old.raster.area, old.raster.crs_wkt))]
                    document.active_area = 0
                document.source_project = old.source_project
                return document
            if len(names) != len(set(names)) or len(names) > 1024 or sum(i.file_size for i in archive.infolist()) > MAX_PROJECT_BYTES:
                raise ValueError('Некорректный состав или слишком большой размер проекта.')
            meta = json.loads(archive.read('workspace.json'))
            if meta.get('format') != DOCUMENT_FORMAT or meta.get('version') != DOCUMENT_VERSION:
                raise ValueError('Неизвестная версия проекта участка.')
            if not 1 <= len(meta['sheets']) <= MAX_SHEETS:
                raise ValueError('Некорректное число листов.')
            doc = Document(fields=validate_schema(meta.get('fields', [])), line_fields=validate_schema(meta.get('line_fields', [])),
                           view=validate_view(meta.get('view', {})))
            for i, record in enumerate(meta['sheets']):
                with tempfile.TemporaryDirectory(prefix='geotrace-read-') as work:
                    snapshot = Path(work)/'sheet.geotrace'
                    snapshot.write_bytes(archive.read(f'sheets/{i}.geotrace'))
                    old = load_project(snapshot)
                if (not isinstance(record['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', record['id'])
                        or any(s.id == record['id'] for s in doc.sheets)):
                    raise ValueError('Некорректный идентификатор листа.')
                embedded = record.get('embedded_source')
                if embedded is not None:
                    if embedded != f'sources/{i}.tif':
                        raise ValueError('Некорректное вложение исходника.')
                    if source_cache is not None:
                        cache = Path(source_cache)/record['id']
                        cache.mkdir(parents=True, exist_ok=True)
                        target = cache/'source.tif'
                        target.write_bytes(archive.read(embedded))
                        for suffix in ('.msk', '.aux.xml'):
                            if embedded+suffix in names:
                                Path(str(target)+suffix).write_bytes(archive.read(embedded+suffix))
                        old.raster.path = str(target)
                doc.sheets.append(Sheet(record['id'], str(record['name']), old.raster, old.result,
                    {int(k): int(v) for k, v in record.get('groups', {}).items()}, bool(record.get('visible', True)), bool(record.get('locked', True))))
                recipe=record.get('recipe',{})
                if not isinstance(recipe,dict) or len(json.dumps(recipe))>100000:
                    raise ValueError('Некорректные настройки распознавания листа.')
                if recipe:
                    groups=recipe.get('groups');palette=recipe.get('palette');samples=recipe.get('samples')
                    if (not isinstance(groups,list) or not 1<=len(groups)<=64 or
                        any(type(v) is not int or not 0<=v<=len(groups) for v in groups) or
                        not isinstance(palette,list) or len(palette)!=len(groups) or
                        not isinstance(samples,list) or len(samples)>64):
                        raise ValueError('Некорректные группы распознавания листа.')
                    for number,item in enumerate(palette,1):
                        if (item.get('id')!=number or not isinstance(item.get('rgb'),list) or len(item['rgb'])!=3 or
                                any(type(c) is not int or not 0<=c<=255 for c in item['rgb'])):
                            raise ValueError('Некорректная палитра листа.')
                    for sample in samples:
                        if (type(sample.get('parent')) is not int or type(sample.get('target')) is not int or
                            not 1<=sample['parent']<sample['target']<=len(groups) or
                            any(not isinstance(sample.get(k),list) or len(sample[k])!=3 or not np.isfinite(sample[k]).all() for k in ('base','picked'))):
                            raise ValueError('Некорректный образец цвета.')
                doc.sheets[-1].recipe=recipe
            doc.classes = meta['classes']
            if len(doc.classes) > 65534 or [c['id'] for c in doc.classes] != list(range(1, len(doc.classes)+1)):
                raise ValueError('Некорректные классы проекта.')
            for cls in doc.classes:
                if not isinstance(cls.get('name'),str) or not cls['name'].strip() or len(cls['name'])>200:
                    raise ValueError('Некорректное имя класса.')
                cls['style'] = validate_style(cls['style'])
            for sheet in doc.sheets:
                if sheet.result is not None and any(sheet.groups.get(k) not in range(1, len(doc.classes)+1)
                                                      for k in range(1, len(sheet.result.colors)+1)):
                    raise ValueError('Некорректные соответствия групп.')
            doc.active_sheet = meta['active_sheet']
            if doc.sheet() is None:
                raise ValueError('Активный лист не найден.')
            from .linework import validate_type
            doc.line_types = [validate_type(item) for item in meta.get('line_types', [])]
            if len({t['id'] for t in doc.line_types})!=len(doc.line_types) or len(doc.line_types)>1000:
                raise ValueError('Некорректные типы линий.')
            doc.lines = [dict(geometry=shape(f['geometry']), properties=f['properties']) for f in meta.get('lines', [])]
            line_ids=set()
            type_ids={t['id'] for t in doc.line_types}
            if len(doc.lines)>100000:
                raise ValueError('Слишком много линейных объектов.')
            for f in doc.lines:
                if (f['geometry'].geom_type != 'LineString' or not f['geometry'].is_valid or f['geometry'].is_empty or
                    f['geometry'].length<=0 or not np.isfinite(f['geometry'].bounds).all() or
                    max(abs(v) for v in f['geometry'].bounds)>25_000_000):
                    raise ValueError('Некорректная линия проекта.')
                props=f['properties'];oid=props.get('object_id')
                if type(oid) is not int or not 1<=oid<2_147_483_647 or oid in line_ids or props.get('type_id') not in type_ids:
                    raise ValueError('Некорректный номер или тип линии.')
                line_ids.add(oid)
                props.update(values_for(doc.line_fields,props))
            doc.areas = [dict(name=a['name'], geometry=shape(a['geometry'])) for a in meta.get('areas', [])]
            for a in doc.areas:
                if a['geometry'].geom_type not in ('Polygon', 'MultiPolygon') or not a['geometry'].is_valid or a['geometry'].is_empty:
                    raise ValueError('Некорректный контур участка.')
            doc.active_area = int(meta.get('active_area', -1))
            if not -1 <= doc.active_area < len(doc.areas):
                raise ValueError('Активный участок не найден.')
            doc.layouts = meta.get('layouts', [])
            from .cartography import validate_layout
            if not isinstance(doc.layouts,list) or len(doc.layouts)>100:
                raise ValueError('Слишком много шаблонов макета.')
            for layout in doc.layouts:
                validate_layout(layout,doc)
            doc.identities = {str(k): int(v) for k, v in meta.get('identities', {}).items()}
            if any(v <= 0 or v >= 2_147_483_647 for v in doc.identities.values()) or len(set(doc.identities.values())) != len(doc.identities):
                raise ValueError('Некорректные номера объектов.')
            doc.dissolve_classes = bool(meta.get('dissolve_classes', False))
            doc.source_project = str(meta.get('source_project', ''))
            doc.settings = dict(meta.get('settings', {}))
            if any(r.get('embedded_source') for r in meta['sheets']):
                doc.settings['portable_sources'] = True
            if 'output.geotrace' in names:
                with tempfile.TemporaryDirectory(prefix='geotrace-output-') as work:
                    target = Path(work)/'output.geotrace'
                    target.write_bytes(archive.read('output.geotrace'))
                    doc.manual = load_project(target).result
            return doc
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError(f'Не удалось открыть проект участка: {exc}') from exc
