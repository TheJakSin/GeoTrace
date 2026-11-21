"""KML processing areas in the raster CRS, with exact vector clipping."""
from dataclasses import replace
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw
from rasterio.features import rasterize, shapes
from rasterio.warp import transform_geom
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union


def polygon_parts(geometry):
    if geometry.geom_type == 'Polygon' and not geometry.is_empty:
        yield geometry
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from polygon_parts(part)


def read_kml(path, raster):
    try:
        root = ET.parse(path).getroot()
        records = []
        places = root.findall('.//{*}Placemark')
        if root.tag.split('}')[-1] == 'Placemark':
            places = [root]
        for placemark in places:
            name = placemark.findtext('{*}name', '').strip() or f'Контур {len(records)+1}'
            polygons = []
            for node in placemark.findall('.//{*}Polygon'):
                def ring(where):
                    element = node.find(where + '/{*}LinearRing/{*}coordinates')
                    if element is None or not element.text:
                        raise ValueError(f'В контуре «{name}» отсутствуют координаты.')
                    coords = [tuple(float(v) for v in pair.split(',')[:2]) for pair in element.text.split()]
                    if (len(coords) < 3 or any(len(p) != 2 for p in coords)
                            or not np.isfinite(coords).all()
                            or any(abs(x) > 180 or abs(y) > 90 for x, y in coords)):
                        raise ValueError(f'Некорректные координаты контура «{name}».')
                    return coords
                outer = ring('{*}outerBoundaryIs')
                holes = []
                for inner in node.findall('{*}innerBoundaryIs'):
                    element = inner.find('{*}LinearRing/{*}coordinates')
                    if element is None or not element.text:
                        raise ValueError(f'Некорректное отверстие контура «{name}».')
                    coords = [tuple(float(v) for v in pair.split(',')[:2]) for pair in element.text.split()]
                    if (len(coords) < 3 or any(len(p) != 2 for p in coords) or not np.isfinite(coords).all()
                            or any(abs(x) > 180 or abs(y) > 90 for x, y in coords)):
                        raise ValueError(f'Некорректное отверстие контура «{name}».')
                    holes.append(coords)
                polygon = Polygon(outer, holes)
                if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
                    raise ValueError(f'Контур «{name}» пересекает сам себя или имеет нулевую площадь.')
                projected = shape(transform_geom('EPSG:4326', raster.crs_wkt, mapping(polygon)))
                if not projected.is_valid or projected.is_empty or not np.isfinite(projected.bounds).all():
                    raise ValueError(f'Не удалось преобразовать координаты контура «{name}».')
                polygons.append(projected)
            if polygons:
                records.append((name, unary_union(polygons)))
        if not records:
            raise ValueError('В KML нужны полигоны с замкнутой внешней границей.')
        if len(records) > 1:
            records.insert(0, ('Все контуры', unary_union([g for _, g in records])))
        return [(f'{name} · {Path(path).name}', geometry) for name, geometry in records]
    except (ET.ParseError, OSError, TypeError, IndexError) as exc:
        raise ValueError(f'Не удалось прочитать KML: {exc}') from exc


def footprint(raster, original=False):
    valid = raster.source_valid if original and raster.source_valid is not None else raster.valid
    result = unary_union([shape(g) for g, _ in shapes(valid.astype('uint8'), mask=valid,
                                                     transform=raster.transform)])
    if not original and raster.area is not None:
        result = result.intersection(raster.area)
    return result


def set_area(raster, geometry, name=''):
    original = raster.source_valid if raster.source_valid is not None else raster.valid
    if geometry is None:
        return replace(raster, valid=original, source_valid=None, area=None, area_name='')
    geometry = geometry.intersection(footprint(raster, original=True))
    geometry = unary_union(list(polygon_parts(geometry)))
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError('Контур KML не пересекает непрозрачную часть карты.')
    mask = rasterize([(geometry, 1)], out_shape=original.shape, transform=raster.transform,
                     all_touched=True, dtype='uint8').astype(bool) & original
    if mask.sum() < 16:
        raise ValueError('Внутри контура слишком мало пикселей карты для обработки.')
    return replace(raster, valid=mask, source_valid=original, area=geometry, area_name=name)


def clip_result(result, raster=None):
    from .core import Result
    raster = result.raster if raster is None else raster
    if raster.area is None:
        return replace(result, raster=raster)
    boundary = footprint(raster)
    next_id = max((f['properties']['object_id'] for f in result.features), default=0)+1
    features = []
    for feature in result.features:
        pieces = sorted(polygon_parts(feature['geometry'].intersection(boundary)), key=lambda g: (-g.area, g.wkb_hex))
        for i, piece in enumerate(pieces):
            if not piece.is_valid:
                raise ValueError('Не удалось обрезать геометрию по области обработки.')
            properties = feature['properties'].copy()
            if i:
                if next_id > 2147483646:
                    raise ValueError('Достигнут предел идентификаторов объектов.')
                properties['object_id'] = next_id
                next_id += 1
            features.append(dict(geometry=piece, properties=properties))
    labels = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                       out_shape=raster.valid.shape, transform=raster.transform, dtype='uint16') if features else np.zeros(raster.valid.shape, np.uint16)
    labels[~raster.valid] = 0
    return Result(raster, labels, features, result.colors, result.fields)


def save_area_preview(raster, geometry, path):
    image = Image.new('RGBA', (raster.valid.shape[1], raster.valid.shape[0]))
    if geometry is not None:
        draw = ImageDraw.Draw(image)
        inverse = ~raster.transform
        for part in polygon_parts(geometry):
            for ring in [part.exterior, *part.interiors]:
                points = [inverse * point for point in ring.coords]
                draw.line(points, fill=(255, 255, 255, 255), width=5)
                draw.line(points, fill=(0, 132, 151, 255), width=2)
    image.save(path)
