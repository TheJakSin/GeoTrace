"""Separate line objects: colour-supported trace proposals and world-space edits."""
import math

import cv2
import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, Point
from shapely.ops import substring
from skimage.morphology import skeletonize

from .spatial import project_geometry
from .styles import validate_style


def validate_type(item):
    if not isinstance(item, dict) or not str(item.get('name', '')).strip():
        raise ValueError('Укажите название типа линий.')
    colors = item.get('colors', [])
    if not isinstance(colors, list) or not 1 <= len(colors) <= 12:
        raise ValueError('Добавьте от 1 до 12 образцов цвета.')
    for color in colors:
        validate_style(dict(color=color), line=True)
    if type(item.get('id')) is not int or not 1 <= item['id'] <= 1_000_000:
        raise ValueError('Некорректный номер типа линий.')
    return dict(id=item['id'], name=str(item['name']).strip()[:80], colors=colors,
                style=validate_style(item.get('style', {}), line=True))


def _paths(skeleton):
    """Trace each undirected skeleton edge once, preserving junctions and loops."""
    y, x = np.nonzero(skeleton)
    pixels = set(zip(x.tolist(), y.tolist()))
    offsets = [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b]
    neighbors = {}
    for p in sorted(pixels):
        near = []
        for dx, dy in offsets:
            q = p[0]+dx, p[1]+dy
            if q not in pixels:
                continue
            if dx and dy and ((p[0]+dx, p[1]) in pixels or (p[0], p[1]+dy) in pixels):
                continue  # No diagonal triangle shortcut at an orthogonal bend.
            near.append(q)
        neighbors[p] = sorted(near)
    visited = set()
    starts = [p for p in sorted(pixels) if len(neighbors[p]) != 2]
    starts.extend(p for p in sorted(pixels) if len(neighbors[p]) == 2)
    for start in starts:
        for next_pixel in neighbors[start]:
            edge = tuple(sorted((start, next_pixel)))
            if edge in visited:
                continue
            visited.add(edge)
            path = [start, next_pixel]
            previous, current = start, next_pixel
            while len(neighbors[current]) == 2 and current != start:
                following = next(p for p in neighbors[current] if p != previous)
                edge = tuple(sorted((current, following)))
                if edge in visited:
                    break
                visited.add(edge)
                path.append(following)
                previous, current = current, following
            yield path


def detect_lines(raster, types, folder=None):
    """Return proposals, never accepted geology. RGB samples alone are ambiguous."""
    from .quality import checkpoint
    lab = cv2.cvtColor(raster.rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    output = []
    for number, raw in enumerate(types):
        item = validate_type(raw)
        checkpoint(folder, f"Ищу линии: {item['name']}…", 5+80*number//max(1, len(types)))
        mask = np.zeros(raster.valid.shape, bool)
        for color in item['colors']:
            rgb = np.array(list(bytes.fromhex(color[1:])), np.uint8)
            center = cv2.cvtColor(rgb.reshape(1, 1, 3), cv2.COLOR_RGB2LAB)[0, 0].astype(float)
            distance = np.linalg.norm(lab-center, axis=2)
            # Widen dark samples along luminance, but never call colour alone proof.
            if max(rgb) < 90:
                distance = np.sqrt(((lab[:, :, 0]-center[0])*.45)**2 + ((lab[:, :, 1:]-center[1:])**2).sum(axis=2))
            mask |= distance <= 24
        mask &= raster.valid
        labels, count = ndimage.label(mask, structure=np.ones((3, 3)))
        accepted = np.zeros(mask.shape, bool)
        for label, location in enumerate(ndimage.find_objects(labels), 1):
            if location is None:
                continue
            component = labels[location] == label
            size = component.sum()
            if size < 8 or max(component.shape) < 10:
                continue
            # A coloured areal fill must not turn into a fault centreline.
            # Component crops often contain no background at all (a straight
            # coloured stroke). Explicit padding measures width, not crop length.
            widths = ndimage.distance_transform_edt(np.pad(component,1))[1:-1,1:-1]
            if np.median(widths[component]) > 3.5:
                continue
            accepted[location] |= component
        skeleton = skeletonize(accepted)
        for path in _paths(skeleton):
            if len(path) < 8:
                continue
            native = LineString([raster.transform*(x+.5, y+.5) for x, y in path])
            native = native.simplify(math.sqrt(abs(raster.transform.determinant))*.35)
            geometry = project_geometry(native, raster.crs_wkt)
            if geometry.is_empty or geometry.length == 0:
                continue
            output.append(dict(geometry=geometry, properties=dict(object_id=len(output)+1, type_id=item['id'],
                name='', note='', review='unreviewed', origin='automatic', confidence='проверить')))
    checkpoint(folder, 'Линии найдены. Проверьте предложения перед применением.', 100)
    return output


def draw_line(points):
    if len(points) < 2 or len(points) > 20000 or not np.isfinite(points).all():
        raise ValueError('Для линии нужны как минимум две разные точки.')
    line = LineString(points)
    if line.length <= 0 or not line.is_valid:
        raise ValueError('Некорректная линия.')
    return line


def split_line(line, point):
    distance = line.project(Point(point))
    if min(distance, line.length-distance) < line.length*1e-8:
        raise ValueError('Разделение должно находиться внутри линии.')
    return [substring(line, 0, distance), substring(line, distance, line.length)]


def join_lines(lines):
    if len(lines) < 2:
        raise ValueError('Выберите минимум две линии одного типа.')
    coords = list(lines[0].coords)
    remaining = list(lines[1:])
    while remaining:
        choices = []
        for index, line in enumerate(remaining):
            for prepend in (False, True):
                for reverse in (False, True):
                    values = list(line.coords)[::-1] if reverse else list(line.coords)
                    distance = math.dist(coords[0], values[-1]) if prepend else math.dist(coords[-1], values[0])
                    choices.append((distance, index, prepend, values))
        _, index, prepend, values = min(choices, key=lambda row: row[:3])
        if prepend:
            coords = values[:-1]+coords if values[-1] == coords[0] else values+coords
        else:
            coords.extend(values[1:] if coords[-1] == values[0] else values)
        remaining.pop(index)
    return draw_line(coords)


def edit_vertex(line, point, remove=False):
    coords = list(line.coords)
    if remove:
        if len(coords) <= 2:
            raise ValueError('В линии должны остаться минимум две вершины.')
        del coords[min(range(len(coords)), key=lambda i: math.dist(coords[i], point))]
    else:
        target = line.project(Point(point))
        distance = 0.
        for i, (a, b) in enumerate(zip(coords, coords[1:]), 1):
            distance += math.dist(a, b)
            if distance >= target:
                p = tuple(line.interpolate(target).coords)[0]
                if min(math.dist(p, a), math.dist(p, b)) < 1e-9:
                    raise ValueError('В этой точке уже есть вершина.')
                coords.insert(i, p)
                break
    return draw_line(coords)


def drag_line(line, point, dx, dy, radius, single=False):
    """Insert a grab point on a long edge; bend a bounded arc or one vertex."""
    grabbed = line.project(Point(point))
    coords = list(line.coords)
    distances = [0.]
    for a, b in zip(coords, coords[1:]):
        distances.append(distances[-1]+math.dist(a, b))
    nearest = min(range(len(coords)), key=lambda i: abs(distances[i]-grabbed))
    if abs(distances[nearest]-grabbed) > max(radius*.1, 1e-9):
        index = next(i for i in range(1, len(distances)) if distances[i] > grabbed)
        coords.insert(index, tuple(line.interpolate(grabbed).coords)[0])
        distances.insert(index, grabbed)
        nearest = index
    output = []
    for i, (x, y) in enumerate(coords):
        weight = float(i == nearest) if single else max(0., 1-abs(distances[i]-grabbed)/max(radius, 1e-9))**2
        output.append((x+dx*weight, y+dy*weight))
    return draw_line(output)
