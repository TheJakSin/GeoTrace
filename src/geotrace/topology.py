"""Remove floating-point cracks, preserving real interior regions and outer edges."""
from shapely.geometry import Polygon


def clean_numeric_interiors(geometry, affine):
    if geometry.geom_type != 'Polygon' or not geometry.interiors:
        return geometry
    # Measure in working pixels. No resampling or snapping of the exterior:
    # reprojection and overlay may leave rings only a few ulps wide.
    from .editing import transform_geometry
    inverse = ~affine
    keep = []
    for ring in geometry.interiors:
        pixel = transform_geometry(Polygon(ring), inverse)
        if pixel.length and 2 * pixel.area / pixel.length <= 1e-7:
            continue
        keep.append(ring.coords)
    if len(keep) == len(geometry.interiors):
        return geometry
    repaired = Polygon(geometry.exterior.coords, keep)
    return repaired if repaired.is_valid else geometry


def clean_result_interiors(result):
    if result is None:
        return None
    features = []
    changed = False
    for feature in result.features:
        before = feature['geometry']
        after = clean_numeric_interiors(before, result.raster.transform)
        changed |= after is not before
        features.append(dict(geometry=after, properties=dict(feature['properties'])))
    # Old merges could also leave automatic, machine-width objects occupying a
    # collapsed hole. Remove them only when another real object covers them.
    from .automatic import protected_feature
    from .editing import transform_geometry
    from shapely.strtree import STRtree
    tree = STRtree([f['geometry'] for f in features])
    numeric = set()
    for i, feature in enumerate(features):
        pixel = transform_geometry(feature['geometry'], ~result.raster.transform)
        if pixel.length and 2 * pixel.area / pixel.length <= 1e-7:
            numeric.add(i)
    removed = set()
    for i in numeric:
        feature = features[i]
        if protected_feature(feature, result.fields):
            continue
        if any(int(j) not in numeric and features[int(j)]['geometry'].covers(feature['geometry']) for j in tree.query(feature['geometry'])):
            removed.add(i)
    if removed:
        features = [f for i, f in enumerate(features) if i not in removed]
        changed = True
    if not changed:
        return result
    from .editing import _result_from_features
    return _result_from_features(result.raster, features, result.colors, result.fields)
