"""Display coordinates and physical measurements are deliberately independent."""
from functools import lru_cache
import math

import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely import segmentize
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.polygon import orient
from shapely.ops import transform

DISPLAY_CRS = 'EPSG:3857'
GEOD = Geod(ellps='WGS84')


@lru_cache(maxsize=64)
def transformer(source, target):
    return Transformer.from_crs(CRS.from_user_input(source), CRS.from_user_input(target), always_xy=True)


def project_geometry(geometry, source, target=DISPLAY_CRS, densify=False):
    if geometry is None or geometry.is_empty:
        return geometry
    src, dst = CRS.from_user_input(source), CRS.from_user_input(target)
    if src == dst:
        return geometry
    if densify:
        geometry = segmentize(geometry, .02 if src.is_geographic else 1000.)
    result = transform(transformer(src.to_string(), dst.to_string()).transform, geometry)
    if not all(math.isfinite(v) for v in result.bounds):
        raise ValueError('Карта выходит за допустимую область рабочей проекции.')
    return result


@lru_cache(maxsize=8192)
def measurements(geometry, crs=DISPLAY_CRS):
    """Ellipsoidal m² and m, including holes and multipart geometries.

    Densify projected segments before inversion: a straight cartographic edge is
    not necessarily the geodesic between its two endpoints.
    """
    if geometry is None or geometry.is_empty:
        return dict(area_m2=0., length_m=0.)
    projected = CRS.from_user_input(crs).is_projected
    shape = segmentize(geometry, 1000. if projected else .01)
    geographic = project_geometry(shape, crs, 'EPSG:4326')
    if geographic.geom_type in ('Polygon', 'MultiPolygon'):
        parts = [geographic] if isinstance(geographic, Polygon) else geographic.geoms
        area = perimeter = 0.
        for part in parts:
            a, p = GEOD.geometry_area_perimeter(orient(part, sign=1.))
            area += abs(a)
            perimeter += p
        return dict(area_m2=float(area), length_m=float(perimeter))
    return dict(area_m2=0., length_m=float(GEOD.geometry_length(geographic)))


def measurement_label(geometry, crs=DISPLAY_CRS):
    values = measurements(geometry, crs)
    if geometry.geom_type in ('Polygon', 'MultiPolygon'):
        return f"{values['area_m2']/10000:,.3f} га · {values['area_m2']/1e6:,.4f} км²".replace(',', ' ')
    return f"{values['length_m']:,.1f} м · {values['length_m']/1000:,.3f} км".replace(',', ' ')


def local_scale(geometry, crs=DISPLAY_CRS):
    """Ground metres per display unit at a location, for a map scale bar."""
    point = geometry.representative_point()
    t = transformer(crs, 'EPSG:4326')
    a, b = t.transform([point.x, point.x+100.], [point.y, point.y])
    return abs(GEOD.inv(a[0], b[0], a[1], b[1])[2])/100.
