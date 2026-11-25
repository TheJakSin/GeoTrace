"""Vector edits preserve the selected union and leave all other objects unchanged."""
import numpy as np
from rasterio.features import rasterize
from shapely.affinity import affine_transform
from shapely.geometry import LineString, Point
from shapely.ops import split, unary_union

from .core import Result
from .attributes import defaults


def transform_geometry(geometry, affine):
    return affine_transform(geometry, [affine.a, affine.b, affine.d, affine.e, affine.c, affine.f])


def edit_geometry(result, object_ids, operation, points=()):
    if operation not in ('merge', 'split', 'boundary'):
        raise ValueError('Неизвестная операция редактирования.')
    if operation == 'merge':
        return merge_polygons(result, object_ids)
    expected = 1 if operation == 'split' else 2
    if len(object_ids) != expected or len(set(object_ids)) != expected:
        raise ValueError('Выберите один полигон для разделения или два соседних для объединения/границы.')
    lookup = {f['properties']['object_id']: f for f in result.features}
    if any(i not in lookup for i in object_ids):
        raise ValueError('Выбранный объект больше не существует.')
    chosen = [lookup[i] for i in object_ids]
    inverse = ~result.raster.transform
    # Pixel coordinates keep tolerances independent of the source CRS and its units.
    polygons = [transform_geometry(f['geometry'], inverse) for f in chosen]
    area = unary_union(polygons)
    if len(polygons) == 2:
        # World-space shared edges avoid round-trip coordinate noise from the inverse affine.
        shared = chosen[0]['geometry'].boundary.intersection(chosen[1]['geometry'].boundary)
        if shared.is_empty or transform_geometry(shared, inverse).length < 1e-6 or area.geom_type != 'Polygon':
            raise ValueError('Нужны два соседних полигона с общей линией границы, а не только общей точкой.')
    if len(points) < 2 or len(points) > 256 or not np.isfinite(np.asarray(points)).all():
        raise ValueError('Проведите линию минимум из двух точек (не более 256).')
    coordinates = [tuple(p) for p in points]
    line = LineString(coordinates)
    if not line.is_simple or line.length < 1e-6:
        raise ValueError('Линия не должна пересекать саму себя или быть замкнутой.')
    # Clicking close to the exterior is enough: extend the ends through the boundary.
    for index, neighbor in ((0, 1), (-1, -2)):
        point = Point(coordinates[index])
        distance = area.exterior.distance(point)
        if distance <= 5:
            direction = np.array(coordinates[index]) - np.array(coordinates[neighbor])
            length = np.linalg.norm(direction)
            if length < 1e-9:
                raise ValueError('Уберите совпадающие соседние точки линии.')
            coordinates[index] = tuple(np.array(coordinates[index]) + direction / length * 12)
        if area.contains(Point(coordinates[index])):
            raise ValueError('Начните и закончите линию за внешним контуром выбранной области.')
    line = LineString(coordinates)
    if not line.is_simple or line.is_ring:
        raise ValueError('Уберите самопересечение линии.')
    pieces = list(split(area, line).geoms)
    if len(pieces) != 2 or any(p.geom_type != 'Polygon' or p.area < 0.25 for p in pieces):
        raise ValueError('Линия должна разделить выбранную область ровно на две части, без мелких осколков.')
    if operation == 'split':
        pieces.sort(key=lambda p: p.area, reverse=True)
        sources = [chosen[0], chosen[0]]
    else:
        # Keep identity with the side that retains most of its old geometry.
        direct = pieces[0].intersection(polygons[0]).area + pieces[1].intersection(polygons[1]).area
        reverse = pieces[1].intersection(polygons[0]).area + pieces[0].intersection(polygons[1]).area
        if abs(direct - reverse) < 1e-7:
            raise ValueError('Неоднозначное соответствие сторон. Проведите новую границу ближе к прежней.')
        if reverse > direct:
            pieces.reverse()
        sources = chosen
    tolerance = max(1e-7, area.area * 1e-10)
    if (any(not p.is_valid or p.is_empty for p in pieces)
            or unary_union(pieces).symmetric_difference(area).area > tolerance
            or sum(p.area for p in pieces) - unary_union(pieces).area > tolerance):
        raise ValueError('Правка нарушает покрытие или создаёт наложение. Измените линию.')
    next_id = max(lookup) + 1
    if operation == 'split' and next_id > 2147483646:
        raise ValueError('Достигнут предел идентификаторов объектов.')
    features = [dict(geometry=f['geometry'], properties=f['properties'].copy())
                for f in result.features if f['properties']['object_id'] not in object_ids]
    for i, (piece, source) in enumerate(zip(pieces, sources)):
        properties = source['properties'].copy()
        properties.update(review='edited', origin='manual_' + operation)
        if operation == 'split' and i == 1:
            properties['object_id'] = next_id
        geometry = transform_geometry(piece, result.raster.transform)
        if not geometry.is_valid:
            raise ValueError('Некорректная геометрия после преобразования координат.')
        features.append(dict(geometry=geometry, properties=properties))
    features.sort(key=lambda f: f['properties']['object_id'])
    labels = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                       out_shape=result.labels.shape, transform=result.raster.transform, dtype='uint16')
    labels[~result.raster.valid] = 0
    return Result(result.raster, labels, features, [list(c) for c in result.colors], result.fields)


def merge_polygons(result, object_ids):
    """Merge edge-connected components, without joining across empty space."""
    from shapely.strtree import STRtree
    if len(object_ids) < 2 or len(set(object_ids)) != len(object_ids):
        raise ValueError('Выберите минимум два соседних полигона.')
    lookup = {f['properties']['object_id']: f for f in result.features}
    if any(oid not in lookup for oid in object_ids):
        raise ValueError('Выбранный объект больше не существует.')
    chosen = [lookup[oid] for oid in object_ids]
    geometries = [f['geometry'] for f in chosen]
    tree = STRtree(geometries)
    parent = list(range(len(chosen)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    inverse = ~result.raster.transform
    for i, geometry in enumerate(geometries):
        for j in tree.query(geometry, predicate='intersects'):
            j = int(j)
            if j <= i:
                continue
            shared = geometry.boundary.intersection(geometries[j].boundary)
            if not shared.is_empty and transform_geometry(shared, inverse).length > 1e-6:
                parent[root(j)] = root(i)
    groups = {}
    for i in range(len(chosen)):
        groups.setdefault(root(i), []).append(i)
    removed, merged = set(), []
    for members in groups.values():
        if len(members) < 2:
            continue
        geometry = unary_union([geometries[i] for i in members])
        from .topology import clean_numeric_interiors
        geometry = clean_numeric_interiors(geometry, result.raster.transform)
        pixel = transform_geometry(geometry, inverse)
        if geometry.geom_type != 'Polygon' or not geometry.is_valid or geometry.is_empty:
            raise ValueError('Объединение создаёт некорректный полигон.')
        total = sum(transform_geometry(geometries[i], inverse).area for i in members)
        if abs(total-pixel.area) > max(1e-7, total*1e-10):
            raise ValueError('Выбранные полигоны перекрываются. Исправьте наложение перед объединением.')
        primary = chosen[members[0]]
        properties = primary['properties'].copy()
        properties.update(review='edited', origin='manual_merge')
        merged.append(dict(geometry=geometry, properties=properties))
        removed.update(object_ids[i] for i in members)
    if not merged:
        raise ValueError('Выберите хотя бы два соседних полигона с общей линией границы, а не только общей точкой.')
    features = [dict(geometry=f['geometry'], properties=f['properties'].copy())
                for f in result.features if f['properties']['object_id'] not in removed]
    return _result_from_features(result.raster, features+merged, [list(c) for c in result.colors], result.fields)


def lasso_indices(result, tree, points):
    """Positive-area intersection includes partially enclosed polygons, not mere touches."""
    from shapely import make_valid
    from shapely.geometry import Polygon
    coordinates = np.asarray(points, dtype=float)
    if coordinates.ndim != 2 or coordinates.shape[1] != 2 or not 3 <= len(coordinates) <= 4096 or not np.isfinite(coordinates).all():
        raise ValueError('Обведите область лассо минимум из трёх точек.')
    region = make_valid(Polygon(coordinates))
    if region.area <= 1e-7:
        return []
    region = transform_geometry(region, result.raster.transform)
    tolerance = abs(result.raster.transform.determinant)*1e-7
    indices = [int(i) for i in tree.query(region, predicate='intersects')
               if result.features[int(i)]['geometry'].intersection(region).area > tolerance]
    return sorted(indices, key=lambda i: result.features[i]['properties']['object_id'])


def _result_from_features(raster, features, colors, fields=None):
    features.sort(key=lambda f: f['properties']['object_id'])
    labels = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                       out_shape=raster.valid.shape, transform=raster.transform, dtype='uint16') if features else np.zeros(raster.valid.shape,np.uint16)
    labels[~raster.valid] = 0
    return Result(raster, labels, features, colors, fields or [])


def _valid_footprint(raster):
    from rasterio.features import shapes
    from shapely.geometry import shape
    from .region import footprint
    return transform_geometry(footprint(raster), ~raster.transform)


def draw_polygon(raster, result, points, class_id=None):
    """Insert a polygon and cut its exact outline from every intersected object."""
    from shapely.geometry import Polygon
    if len(points) < 3 or len(points) > 256 or not np.isfinite(np.asarray(points)).all():
        raise ValueError('Для нового полигона нужны от 3 до 256 точек.')
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area < 0.25:
        raise ValueError('Контур пересекает сам себя или слишком мал. Исправьте точки.')
    if polygon.difference(_valid_footprint(raster)).area > 1e-7:
        raise ValueError('Полигон выходит за непрозрачную часть карты или захватывает NoData.')
    geometry = transform_geometry(polygon, raster.transform)
    features = [dict(geometry=f['geometry'], properties=f['properties'].copy())
                for f in result.features] if result else []
    next_id = max((f['properties']['object_id'] for f in features), default=0) + 1
    colors = [list(c) for c in result.colors] if result else []
    previous = next((f for f in features if f['properties']['origin'] == 'manual_draw'), None)
    if class_id:
        if type(class_id) is not int or not 1 <= class_id <= len(colors):
            raise ValueError('Выберите существующую группу нового полигона.')
    elif previous:
        class_id = previous['properties']['class_id']
    else:
        colors.append([75, 183, 168])
        class_id = len(colors)
    from shapely.strtree import STRtree
    from .region import polygon_parts
    from .topology import clean_numeric_interiors
    geometries = [f['geometry'] for f in features]
    hits = set(map(int, STRtree(geometries).query(geometry, predicate='intersects'))) if features else set()
    cut = []
    for index, feature in enumerate(features):
        if index not in hits or feature['geometry'].intersection(geometry).area <= abs(raster.transform.determinant)*1e-10:
            cut.append(feature)
            continue
        remainder = sorted(polygon_parts(feature['geometry'].difference(geometry)), key=lambda p: p.area, reverse=True)
        for number, piece in enumerate(remainder):
            props = dict(feature['properties'], review='edited', origin='manual_draw_cut')
            if number:
                props['object_id'] = next_id
                next_id += 1
            cut.append(dict(geometry=clean_numeric_interiors(piece, raster.transform), properties=props))
    features = cut
    if next_id > 2147483646 or len(colors) > 65534:
        raise ValueError('Достигнут предел объектов или групп.')
    features.append(dict(geometry=geometry, properties=dict(object_id=next_id, class_id=class_id,
                         note='', review='edited', origin='manual_draw',
                         **defaults(result.fields if result else []))))
    return _result_from_features(raster, features, colors, result.fields if result else [])


def _ring_vertices(polygon):
    return [np.asarray(r.coords)[:-1] for r in [polygon.exterior, *polygon.interiors]]


def _move_ring(ring, source, destination, candidates):
    """Node incident segments consistently before moving the shared node."""
    output = []
    coords = np.asarray(ring.coords)
    for a, b in zip(coords[:-1], coords[1:]):
        vector = b-a
        squared = float(vector @ vector)
        if squared < 1e-20:
            continue
        t = float((source-a) @ vector / squared)
        incident = -1e-9 <= t <= 1+1e-9 and np.linalg.norm(a + np.clip(t,0,1)*vector-source) < 1e-6
        if incident:
            fractions = (candidates-a) @ vector / squared
            distances = np.linalg.norm(candidates - (a + fractions[:,None]*vector), axis=1)
            mask = (fractions > 1e-9) & (fractions < 1-1e-9) & (distances < 1e-6)
            nodes = [a, *candidates[mask][np.argsort(fractions[mask])]]
        else:
            nodes = [a]
        for node in nodes:
            value = destination if np.linalg.norm(node-source) < 1e-6 else node
            if not output or np.linalg.norm(value-output[-1]) > 1e-9:
                output.append(tuple(value))
    return output


def _move_polygon(polygon, source, destination, candidates):
    from shapely.geometry import Polygon
    return Polygon(_move_ring(polygon.exterior, source, destination, candidates),
                   [_move_ring(r, source, destination, candidates) for r in polygon.interiors])


def move_vertex(result, object_id, points):
    """Move all occurrences of a vertex, including neighbors where it lies on an edge.

    A local node on each incident edge is inserted at the closest existing vertex. This
    preserves shared topology even if one neighbor omitted collinear vertices.
    """
    if len(points) != 2 or np.asarray(points).shape != (2,2) or not np.isfinite(points).all():
        raise ValueError('Укажите исходную вершину и её новое положение.')
    source, destination = np.asarray(points, dtype=float)
    if np.linalg.norm(destination-source) < 1e-6:
        raise ValueError('Вершина осталась на месте.')
    inverse = ~result.raster.transform
    polygons = [transform_geometry(f['geometry'], inverse) for f in result.features]
    selected = next((p for p,f in zip(polygons,result.features) if f['properties']['object_id']==object_id), None)
    if selected is None or min(np.linalg.norm(vertices-source, axis=1).min()
                               for vertices in _ring_vertices(selected)) > 1e-6:
        raise ValueError('Выберите существующую вершину выделенного полигона.')
    affected = [i for i,p in enumerate(polygons) if p.boundary.distance(Point(source)) < 1e-6]
    # Collect candidates from all rings so a T-junction halfway along a neighbor edge
    # becomes a common node. Filtering and insertion apply only to incident segments.
    candidates = np.unique(np.vstack([source, *[v for p in polygons for v in _ring_vertices(p)]]), axis=0)
    changed = {i:_move_polygon(polygons[i],source,destination,candidates) for i in affected}
    if any(not p.is_valid or p.area < 0.25 for p in changed.values()):
        raise ValueError('Перемещение создаёт самопересечение, схлопывание или некорректное отверстие.')
    footprint = _valid_footprint(result.raster)
    if any(p.difference(footprint).area > 1e-7 for p in changed.values()):
        raise ValueError('Новая граница выходит за карту или захватывает NoData.')
    before = unary_union(polygons)
    parts = [before] if before.geom_type == 'Polygon' else list(before.geoms)
    expected = unary_union([_move_polygon(p,source,destination,candidates)
                            if p.boundary.distance(Point(source)) < 1e-6 else p for p in parts])
    after_polygons = [changed.get(i,p) for i,p in enumerate(polygons)]
    after = unary_union(after_polygons)
    tolerance = max(1e-7, before.area * 1e-10)
    if (not expected.is_valid or after.symmetric_difference(expected).area > tolerance
            or sum(p.area for p in after_polygons)-after.area > tolerance):
        raise ValueError('Перемещение создаёт щель или наложение. Уменьшите сдвиг либо замените общую границу линией.')
    features = []
    for i, feature in enumerate(result.features):
        properties = feature['properties'].copy()
        geometry = feature['geometry']
        if i in changed:
            geometry = transform_geometry(changed[i],result.raster.transform)
            if not geometry.is_valid:
                raise ValueError('Некорректная геометрия после преобразования координат.')
            properties.update(origin='manual_vertex',review='edited')
        features.append(dict(geometry=geometry,properties=properties))
    return _result_from_features(result.raster, features, [list(c) for c in result.colors], result.fields)
