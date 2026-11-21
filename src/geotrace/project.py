"""Portable project snapshots: bounded raster data, JSON geometry, atomic replacement.

No pickle or executable objects. The original full-resolution TIFF is never modified.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import zipfile

from affine import Affine
import numpy as np
from rasterio.crs import CRS
from shapely.geometry import mapping, shape

from .core import Raster, Result
from .attributes import validate_schema, values_for, SUGGESTED_FIELDS

FORMAT = 'geotrace-project'
VERSION = 4
MAX_PIXELS = 1600 * 1600
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


VIEW_DEFAULTS = dict(raster_visible=True, raster_opacity=1., polygons_visible=True,
                     polygons_opacity=.55, area_visible=True, basemap_visible=False,
                     basemap_source='eox2016', basemap_file='')


def validate_view(values):
    if not isinstance(values, dict):
        raise ValueError('Некорректные настройки слоёв.')
    result = dict(VIEW_DEFAULTS)
    for key in result:
        if key not in values:
            continue
        value = values[key]
        if key == 'basemap_source':
            if value not in ('eox2016', 'yandex', 'local'):
                raise ValueError('Неизвестный источник подложки.')
        elif key == 'basemap_file':
            if not isinstance(value, str):
                raise ValueError('Некорректный путь подложки.')
        elif key.endswith('_visible'):
            if type(value) is not bool:
                raise ValueError('Некорректная видимость слоя.')
        elif type(value) not in (int, float) or not np.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Некорректная прозрачность слоя.')
        result[key] = value
    return result


@dataclass
class Project:
    raster: Raster
    result: Result | None
    points: list
    roi: tuple | None
    source_project: str = ''
    view: dict = field(default_factory=dict)


def save_project(path, raster, result=None, points=(), roi=None, source_project='', view=None):
    """Replace only a complete snapshot; failed writes leave the previous file intact."""
    path = Path(path)
    if path.suffix.lower() != '.geotrace':
        raise ValueError('Проект должен иметь расширение .geotrace.')
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(format=FORMAT, version=VERSION,
                    saved_at=datetime.now(timezone.utc).isoformat(),
                    source_project=str(source_project),
                    raster=dict(path=raster.path, transform=list(raster.transform)[:6],
                                crs_wkt=raster.crs_wkt, crs_label=raster.crs_label,
                                source_size=list(raster.source_size)),
                    has_result=result is not None, colors=result.colors if result else [],
                    points=list(points), roi=roi, fields=result.fields if result else [],
                    area=mapping(raster.area) if raster.area is not None else None,
                    area_name=raster.area_name, view=validate_view(view or {}))
    features = [dict(geometry=mapping(f['geometry']), properties=f['properties'])
                for f in result.features] if result else []
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w+b') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                archive.writestr('project.json', json.dumps(metadata, ensure_ascii=False, allow_nan=False))
                archive.writestr('features.json', json.dumps(features, ensure_ascii=False, allow_nan=False))
                arrays = dict(rgb=raster.rgb, valid=raster.valid)
                if raster.source_valid is not None:
                    arrays['source_valid'] = raster.source_valid
                if result is not None:
                    arrays['labels'] = result.labels
                for name, array in arrays.items():
                    buffer = BytesIO()
                    np.save(buffer, array, allow_pickle=False)
                    archive.writestr(name + '.npy', buffer.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def load_project(path):
    """Load a self-contained working raster even when the source TIFF has moved."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 8 or len({i.filename for i in entries}) != len(entries):
                raise ValueError('Некорректный состав проекта.')
            if sum(i.file_size for i in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError('Проект превышает допустимый размер текущей версии.')
            meta = json.loads(archive.read('project.json'))
            if meta.get('format') != FORMAT or meta.get('version') not in (1, 2, 3, VERSION):
                raise ValueError('Неизвестный формат или версия проекта.')

            def array(name, shape_limit, kinds):
                raw = archive.read(name + '.npy')
                # Inspect the header before numpy allocates any array from an untrusted shape.
                stream = BytesIO(raw)
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    dimensions, _, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    dimensions, _, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError('Неподдерживаемый формат массива проекта.')
                size = 1
                for dimension in dimensions:
                    size *= dimension
                if len(dimensions) not in (2, 3) or not all(d > 0 for d in dimensions) or size > shape_limit or dtype.kind not in kinds or dtype.itemsize > 2:
                    raise ValueError('Некорректный размер или тип массива проекта.')
                return np.load(BytesIO(raw), allow_pickle=False)

            rgb = array('rgb', MAX_PIXELS * 3, 'u')
            valid = array('valid', MAX_PIXELS, 'b')
            if rgb.dtype != np.uint8 or rgb.shape != (*valid.shape, 3) or valid.ndim != 2 or max(valid.shape) > 1600:
                raise ValueError('Несогласованные размеры рабочей карты.')
            info = meta['raster']
            transform = Affine(*info['transform'])
            if not np.isfinite(tuple(transform)).all() or transform.determinant == 0:
                raise ValueError('Некорректная географическая привязка проекта.')
            crs = CRS.from_wkt(info['crs_wkt'])
            source_size = tuple(info['source_size'])
            if len(source_size) != 2 or any(type(v) is not int or v <= 0 for v in source_size):
                raise ValueError('Некорректный размер исходной карты.')
            raster = Raster(str(info['path']), rgb, valid, transform, crs.to_wkt(), crs.to_string(), source_size)
            if meta.get('area') is not None:
                from .region import set_area
                original = array('source_valid', MAX_PIXELS, 'b')
                if original.shape != valid.shape or (valid & ~original).any():
                    raise ValueError('Некорректная маска области обработки.')
                geometry = shape(meta['area'])
                if geometry.geom_type not in ('Polygon', 'MultiPolygon') or geometry.is_empty or not geometry.is_valid:
                    raise ValueError('Некорректный контур области обработки.')
                raster = set_area(Raster(raster.path, rgb, original, transform, raster.crs_wkt,
                                         raster.crs_label, source_size), geometry, str(meta.get('area_name', '')))
                if not np.array_equal(raster.valid, valid):
                    raise ValueError('Маска области не соответствует её контуру.')
            result = None
            fields = validate_schema(meta.get('fields', []))
            if meta['has_result']:
                labels = array('labels', MAX_PIXELS, 'u')
                colors = meta['colors']
                if (labels.shape != valid.shape or len(colors) > 65534
                        or any(len(c) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in colors)
                        or labels.max() > len(colors) or (labels[~valid] != 0).any()):
                    raise ValueError('Некорректные группы полигонов проекта.')
                records = json.loads(archive.read('features.json'))
                # Old releases always stored both geology keys, including on
                # empty layers. Migrate populated keys into editable project fields.
                for definition in SUGGESTED_FIELDS[:2]:
                    key = definition['key']
                    if key not in {f['key'] for f in fields}:
                        if any(record['properties'].get(key) for record in records):
                            fields.extend(validate_schema([definition]))
                        else:
                            for record in records:
                                record['properties'].pop(key,None)
                features, ids = [], set()
                for record in records:
                    geometry, properties = shape(record['geometry']), record['properties']
                    oid, cid = properties['object_id'], properties['class_id']
                    if (geometry.geom_type != 'Polygon' or geometry.is_empty or not geometry.is_valid
                            or type(oid) is not int or oid <= 0 or oid > 2147483646 or oid in ids
                            or type(cid) is not int or not 1 <= cid <= len(colors)
                            or any(not isinstance(properties[k], str) for k in ('note','review','origin'))):
                        raise ValueError('Некорректная геометрия или атрибуты проекта.')
                    properties.update(values_for(fields,properties))
                    ids.add(oid)
                    features.append(dict(geometry=geometry, properties=properties))
                result = Result(raster, labels, features, colors, fields)
            points, roi = meta.get('points', []), meta.get('roi')
            h, w = valid.shape
            if roi is not None:
                if (len(roi) != 4 or any(type(v) is not int for v in roi)
                        or not (0 <= roi[0] < roi[2] <= w and 0 <= roi[1] < roi[3] <= h)):
                    raise ValueError('Некорректная рамка подсказок.')
                roi = tuple(roi)
            if len(points) > 64 or (points and roi is None):
                raise ValueError('Некорректные подсказки проекта.')
            for p in points:
                if (len(p) != 3 or any(type(v) is not int for v in p) or p[2] not in (0, 1)
                        or not (roi[0] <= p[0] < roi[2] and roi[1] <= p[1] < roi[3]) or not valid[p[1],p[0]]):
                    raise ValueError('Некорректная точка подсказки.')
            return Project(raster, result, [tuple(p) for p in points], roi, str(meta.get('source_project', '')), validate_view(meta.get('view', {})))
    except (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile, EOFError) as exc:
        raise ValueError(f'Не удалось открыть проект: {exc}') from exc
