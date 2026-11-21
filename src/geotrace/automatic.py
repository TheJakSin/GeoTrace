"""Stage repeat digitization around exact, protected vector geometries."""
import numpy as np
from rasterio.features import rasterize
from shapely.ops import unary_union

from .attributes import defaults
from .core import Result


def protected_feature(feature, fields):
    props = feature['properties']
    return (props['review'] in ('edited', 'accepted')
            or props.get('note')
            or any(props.get(k) for k in ('unit_index','unit_name') if k not in {f['key'] for f in fields})
            or any(props.get(f['key'], f['default']) != f['default'] for f in fields))


def polygon_parts(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type == 'Polygon':
        yield geometry
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from polygon_parts(part)


def preserve_work(generated, previous, partial=False):
    """Return a new result; protected IDs, geometry and attributes remain exact.

    New pieces receive fresh IDs and current schema defaults. The raster labels
    are only a display cache; clipping uses vector geometry, including subpixels.
    """
    if previous is None:
        return generated
    raster = generated.raster
    if (previous.labels.shape != generated.labels.shape
            or previous.raster.transform != raster.transform
            or previous.raster.crs_wkt != raster.crs_wkt
            or not np.array_equal(previous.raster.valid, raster.valid)):
        raise ValueError('Повторное выделение относится к другой карте.')
    kept = [dict(geometry=f['geometry'], properties=f['properties'].copy())
            for f in previous.features if protected_feature(f, previous.fields)]
    protected = unary_union([f['geometry'] for f in kept])
    footprint = unary_union([f['geometry'] for f in generated.features])
    tolerance = abs(raster.transform.determinant) * max(1e-6, raster.valid.sum() * 1e-10)
    if partial:
        footprint = footprint.union(protected)
    if protected.difference(footprint).area > tolerance:
        raise ValueError('Сохранённые полигоны выходят за область нового выделения.')
    # Reuse free palette slots without renumbering protected classes or growing
    # the palette on every repeated run.
    used = {f['properties']['class_id'] for f in kept}
    colors = [list(c) for c in previous.colors[:max(used, default=0)]]
    slots = iter(i for i in range(1, 65535) if i not in used)
    classes = {}
    for cls, color in enumerate(generated.colors, 1):
        target = next(slots, None)
        if target is None:
            raise ValueError('Достигнут предел количества цветовых групп.')
        classes[cls] = target
        if target > len(colors):
            colors.append(list(color))
        else:
            colors[target - 1] = list(color)
    next_id = max((f['properties']['object_id'] for f in previous.features), default=0) + 1
    features = list(kept)
    values = defaults(previous.fields)
    for feature in generated.features:
        geometry = feature['geometry'].difference(protected) if kept else feature['geometry']
        for piece in polygon_parts(geometry):
            if not piece.is_valid:
                raise ValueError('Не удалось согласовать границу с ручной правкой.')
            if next_id > 2147483646:
                raise ValueError('Достигнут предел идентификаторов объектов.')
            props = dict(feature['properties'], **values)
            props.update(object_id=next_id, class_id=classes[props['class_id']])
            features.append(dict(geometry=piece, properties=props))
            next_id += 1
    union = unary_union([f['geometry'] for f in features])
    if (union.symmetric_difference(footprint).area > tolerance
            or sum(f['geometry'].area for f in features) - union.area > tolerance):
        raise ValueError('Повторное выделение создаёт пробелы или наложения. Результат не применён.')
    features.sort(key=lambda f: f['properties']['object_id'])
    labels = rasterize([(f['geometry'], f['properties']['class_id']) for f in features],
                       out_shape=raster.valid.shape, transform=raster.transform, dtype='uint16') if features else np.zeros(raster.valid.shape,np.uint16)
    labels[~raster.valid] = 0
    return Result(raster, labels, features, colors, [dict(f) for f in previous.fields])
