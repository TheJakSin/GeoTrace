"""GIS operations independent of the desktop UI.

Color clusters are proposals, NOT inferred geological units.
The raster grid is the common boundary model for this first experiment.
"""
from dataclasses import dataclass, field
from pathlib import Path
import os
import tempfile

import cv2
import fiona
import numpy as np
import rasterio
from affine import Affine
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.features import shapes, sieve, rasterize
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from .attributes import defaults, values_for
from .preprocessing import clean_colors
from .raster_colors import read_rgb


@dataclass
class Raster:
    path: str
    rgb: np.ndarray
    valid: np.ndarray
    transform: Affine
    crs_wkt: str
    crs_label: str
    source_size: tuple
    area: object = None
    source_valid: np.ndarray | None = None
    area_name: str = ''


@dataclass
class Result:
    raster: Raster
    labels: np.ndarray
    features: list
    colors: list
    fields: list = field(default_factory=list)


def read_raster(path, max_side=1600):
    with rasterio.open(path) as src:
        if src.crs is None or src.transform == Affine.identity():
            raise ValueError("Нужен TIFF с системой координат и географической привязкой.")
        scale = min(1.0, max_side / max(src.width, src.height))
        w, h = max(1, round(src.width * scale)), max(1, round(src.height * scale))
        rgb = read_rgb(src, w, h)
        valid = src.dataset_mask(out_shape=(h, w), resampling=Resampling.nearest) > 0
        if valid.sum() < 16:
            raise ValueError("В выбранном файле недостаточно непрозрачных пикселей.")
        transform = src.transform * Affine.scale(src.width / w, src.height / h)
        return Raster(str(Path(path).resolve()), np.moveaxis(rgb, 0, -1).copy(), valid,
                      transform, src.crs.to_wkt(), src.crs.to_string(), (src.width, src.height))


def segment(raster, classes=7, min_pixels=80, profile="baseline"):
    """Deterministic baseline: inpaint dark ink, Lab clustering, sieve, polygonize."""
    cv2.setNumThreads(2)
    if not 2 <= classes <= 16:
        raise ValueError("Число цветовых групп должно быть от 2 до 16.")
    rgb, valid = raster.rgb.copy(), raster.valid
    if profile not in ('baseline', 'scan'):
        raise ValueError('Неизвестный режим автоматического выделения.')
    if profile == 'scan':
        rgb = clean_colors(rgb, valid)
    else:
        # Fill dark ink and nodata before filtering.
        usable = valid & (rgb.max(axis=2) > 95)
        if not usable.any():
            raise ValueError("Не удалось найти цветной фон карты.")
        indices = ndimage.distance_transform_edt(~usable, return_distances=False, return_indices=True)
        rgb = cv2.medianBlur(rgb[tuple(indices)], 5)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    light_weight = 0.4 if profile == 'scan' else 1.0
    lab[:, :, 0] *= light_weight
    samples = lab[valid]
    rng = np.random.default_rng(42)
    if len(samples) > 50000:
        samples = samples[rng.choice(len(samples), 50000, replace=False)]
    classes = min(classes, len(np.unique(samples, axis=0)))
    if classes < 2:
        raise ValueError("На карте недостаточно различных цветов для выделения областей.")
    cv2.setRNGSeed(42)
    _, _, centers = cv2.kmeans(samples, classes, None,
                              (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 35, 0.2),
                              3, cv2.KMEANS_PP_CENTERS)
    centers = centers[np.argsort(centers[:, 0])]
    labels = np.zeros(valid.shape, dtype=np.uint8)
    # Row chunks keep distance arrays bounded.
    for start in range(0, labels.shape[0], 96):
        block = lab[start:start + 96]
        distances = ((block[:, :, None, :] - centers[None, None, :, :]) ** 2).sum(axis=3)
        labels[start:start + 96] = distances.argmin(axis=2).astype(np.uint8) + 1
    labels[~valid] = 0
    labels = sieve(labels, size=max(1, int(min_pixels)), mask=valid, connectivity=4)
    centers[:, 0] /= light_weight
    colors = cv2.cvtColor(centers.clip(0, 255).astype(np.uint8)[None, :, :],
                          cv2.COLOR_LAB2RGB)[0].tolist()
    features = vectorize(labels, raster)
    if profile == 'scan':
        for feature in features:
            feature['properties']['origin'] = 'color_scan'
    from .region import clip_result
    return clip_result(Result(raster, labels, features, colors))


def vectorize(labels, raster):
    features = []
    for geo, value in shapes(labels, mask=raster.valid & (labels > 0), transform=raster.transform, connectivity=4):
        geom = shape(geo)
        if not geom.is_valid or geom.is_empty:
            raise ValueError("Получена некорректная геометрия; экспорт остановлен.")
        features.append({"geometry": geom, "properties": {
            "object_id": len(features) + 1, "class_id": int(value),
            "note": "",
            "review": "unreviewed", "origin": "color_clusters",
        }})
    return features


def save_preview(raster, path):
    rgba = np.dstack((raster.rgb, raster.valid.astype(np.uint8) * 255))
    rgba[~raster.valid] = 0
    Image.fromarray(rgba).save(path)


def save_overlay(result, path, outlines=True):
    palette = np.array([[0, 0, 0]] + result.colors, dtype=np.uint8)
    rgba = np.dstack((palette[result.labels], ((result.labels > 0) & result.raster.valid).astype(np.uint8) * 210))
    image = Image.fromarray(rgba)
    draw = ImageDraw.Draw(image)
    inverse = ~result.raster.transform
    # Geometry, rather than color-class changes, defines the visible object boundaries.
    for feature in result.features if outlines else []:
        geometry = feature['geometry']
        for ring in [geometry.exterior, *geometry.interiors]:
            draw.line([inverse * point for point in ring.coords], fill=(19,45,43,255), width=1)
    image.save(path)


def export_gpkg(result, path):
    """Publish a NEW file only; never replace an existing GIS dataset."""
    path = Path(path)
    if path.exists():
        raise FileExistsError("Файл уже существует. Выберите новое имя, чтобы сохранить обе версии.")
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = {"geometry": "Polygon", "properties": {
        "object_id": "int", "class_id": "int",
        "note": "str", "review": "str", "origin": "str",
    }}
    field_types = dict(text='str',integer='int64',number='float',boolean='int',date='date',choice='str')
    schema['properties'].update({f['key']:field_types[f['type']] for f in result.fields})
    records=[]
    for feature in result.features:
        properties=feature['properties'].copy()
        # Handle in-memory results supplied by callers of the older API.
        for key in ('unit_index','unit_name'):
            if key in properties and key not in schema['properties']:
                schema['properties'][key]='str'
        try:
            properties.update(values_for(result.fields,properties,enforce_required=True))
        except ValueError as exc:
            raise ValueError(f"Полигон № {properties['object_id']}: {exc}") from exc
        for f in result.fields:
            if f['type']=='boolean' and properties[f['key']] is not None:
                properties[f['key']]=int(properties[f['key']])
        records.append(dict(geometry=mapping(feature['geometry']),properties=properties))
    fd, temp = tempfile.mkstemp(suffix='.gpkg', prefix='.geotrace-', dir=path.parent)
    os.close(fd)
    os.unlink(temp)
    try:
        with fiona.open(temp, 'w', driver='GPKG', layer='geological_proposals',
                        crs_wkt=result.raster.crs_wkt, schema=schema, encoding='UTF-8') as dst:
            for record in records:
                for key in schema['properties']:
                    record['properties'].setdefault(key,None)
            dst.writerecords(records)
        # Exclusive creation also handles a destination created during export.
        os.link(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return str(path)


def apply_proposal(raster, result, proposal):
    """Replace only proposed pixels, retaining attributes and identities of remaining objects.

    Edited/accepted objects are protected. A later correction must undo their acceptance first.
    Shared edges come from the same pixel grid; no independent polygon smoothing is performed.
    """
    mask = np.asarray(proposal.mask, dtype=bool)
    if mask.shape != raster.valid.shape or (mask & ~raster.valid).any() or not mask.any():
        raise ValueError('Предложение пустое или выходит за допустимую область карты.')
    if result is None:
        result = Result(raster, np.zeros(raster.valid.shape, dtype=np.uint16), [], [])
    if len(result.colors) >= 65534:
        raise ValueError('Достигнут предел количества групп.')
    by_id = {f['properties']['object_id']: f for f in result.features}
    if by_id:
        ids = rasterize([(f['geometry'], k) for k,f in by_id.items()],
                        out_shape=mask.shape, transform=raster.transform, dtype=np.int32)
    else:
        ids = np.zeros(mask.shape, np.int32)
    protected = [f['geometry'] for f in result.features if f['properties']['review'] in ('edited', 'accepted')]
    if protected:
        proposed_area = unary_union([shape(g) for g, value in shapes(mask.astype('uint8'), mask=mask,
                                                                  transform=raster.transform)])
        pixel_area = abs(raster.transform.determinant)
        candidates = STRtree(protected).query(proposed_area, predicate='intersects')
        if any(protected[i].intersection(proposed_area).area > pixel_area * 1e-7 for i in candidates):
            raise ValueError('Область пересекает уже принятую или отредактированную геометрию. Уточните выделение.')
    touched = set(int(v) for v in np.unique(ids[mask]) if v)
    if any(by_id[k]['properties']['review'] in ('edited', 'accepted') for k in touched):
        raise ValueError('Область пересекает уже принятую или отредактированную геометрию. '
                         'Уточните выделение либо отмените её принятие.')
    next_id = max(by_id, default=0) + 1
    new_id = next_id
    next_id += 1
    ids[mask] = new_id
    class_id = len(result.colors) + 1
    labels = result.labels.astype(np.uint16, copy=True)
    labels[mask] = class_id
    features = [{'geometry': f['geometry'], 'properties': f['properties'].copy()}
                for k,f in by_id.items() if k not in touched]
    changed = np.isin(ids, [*touched, new_id])
    grouped = {}
    for geo, value in shapes(ids, mask=changed, transform=raster.transform):
        geom = shape(geo)
        if not geom.is_valid:
            raise ValueError('Не удалось построить корректную общую границу.')
        grouped.setdefault(int(value), []).append(geom)
    for key, geometries in grouped.items():
        if key == new_id:
            props = dict(object_id=new_id, class_id=class_id, note='',
                         review='accepted', origin='prompt_' + proposal.method)
            props.update(defaults(result.fields))
        else:
            props = by_id[key]['properties'].copy()
        for i, geom in enumerate(sorted(geometries, key=lambda g:g.area, reverse=True)):
            current = props.copy()
            if i:
                current['object_id'] = next_id
                next_id += 1
            features.append({'geometry':geom, 'properties':current})
    from .region import clip_result
    return clip_result(Result(raster, labels, sorted(features, key=lambda f:f['properties']['object_id']),
                  [*result.colors, [244, 170, 61]], result.fields))
