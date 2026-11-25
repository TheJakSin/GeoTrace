"""Clipped, simplified drawing paths; the stored vector geometry stays exact."""
from shapely.geometry import box, LineString
from shapely.strtree import STRtree

from .document import line_parts
from .editing import transform_geometry
from .styles import validate_style


def polygon_paths(result, document, bounds, zoom, selected=(), drag=None, tree=None, cuts=None):
    if result is None or not result.features or zoom <= 0:
        return []
    x, y, width, height = bounds
    padding = 8 / zoom
    region = box(x-padding, y-padding, x+width+padding, y+height+padding)
    world = transform_geometry(region, result.raster.transform)
    tree = tree or STRtree([f['geometry'] for f in result.features])
    hidden = set(document.settings.get('hidden_classes', []))
    selected = set(selected)
    outlines = []
    if cuts is None:
        from .sheet_seams import technical_boundaries
        cuts = technical_boundaries(document, result)
    arc = LineString(drag.pixels).buffer(1e-6) if drag is not None else None
    owners = {i for i, _ in drag.owners} if drag is not None else set()
    for index in tree.query(world):
        feature = result.features[int(index)]
        cid = feature['properties']['class_id']
        if cid in hidden:
            continue
        style = document.classes[cid-1]['style'] if cid <= len(document.classes) else validate_style({})
        boundary = feature['geometry'].boundary
        if int(index) in cuts and feature['properties']['object_id'] not in selected:
            boundary = boundary.difference(cuts[int(index)])
        boundary = transform_geometry(boundary, ~result.raster.transform)
        if int(index) in owners:
            boundary = boundary.difference(arc)
        boundary = boundary.intersection(region).simplify(.18 / zoom, preserve_topology=False)
        for line in line_parts(boundary):
            outlines.append(dict(points=[[float(a), float(b)] for a, b in line.coords],
                color=style['outline'], width=style['width'], selected=feature['properties']['object_id'] in selected))
    return outlines
