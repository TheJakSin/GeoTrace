"""Pick nearby edges in screen-independent working pixels and reshape a common arc."""
from dataclasses import dataclass

import numpy as np
from shapely import STRtree, set_precision
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import linemerge, unary_union

from .editing import transform_geometry, _result_from_features, _valid_footprint


def parts(geometry, kind):
    if geometry.is_empty:
        return
    if geometry.geom_type == kind:
        yield geometry
    elif hasattr(geometry, 'geoms'):
        for child in geometry.geoms:
            yield from parts(child, kind)


def distances(coords):
    return np.r_[0., np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))]


@dataclass
class Drag:
    owners: list
    world: np.ndarray
    pixels: np.ndarray
    weights: np.ndarray
    length: float


class EdgeIndex:
    def __init__(self, result):
        self.result = result
        self.inverse = ~result.raster.transform
        chunks, self.owners = [], []
        for i, feature in enumerate(result.features):
            polygon = transform_geometry(feature['geometry'], self.inverse)
            for j, ring in enumerate([polygon.exterior, *polygon.interiors]):
                coords = np.asarray(ring.coords)
                for start in range(0,len(coords)-1,32):
                    chunks.append(LineString(coords[start:start+33]))
                    self.owners.append((i,j))
        self.tree = STRtree(chunks)

    def hit(self, x, y, tolerance):
        if not np.isfinite((x,y,tolerance)).all() or tolerance<=0:
            return None
        point = Point(x,y)
        nearby = self.tree.query(box(x-tolerance,y-tolerance,x+tolerance,y+tolerance))
        if not len(nearby):
            return None
        index = min(nearby,key=lambda k:(self.tree.geometries[k].distance(point),k))
        chunk = self.tree.geometries[index]
        snap = chunk.interpolate(chunk.project(point))
        if point.distance(snap)<=tolerance:
            return (*self.owners[index],snap)

    def plan(self, x, y, tolerance, single, polygons):
        hit = self.hit(x,y,tolerance)
        if hit is None:
            return None
        i,j,point = hit
        result = self.result
        owner = result.features[i]['geometry']
        world_ring = [owner.exterior,*owner.interiors][j]
        def pixel_line(outline):
            return set_precision(LineString(transform_geometry(outline,self.inverse).coords),1e-8)
        ring = pixel_line(world_ring)
        originals = [np.asarray(world_ring.coords)]
        radius = 5*tolerance
        # Single-node edits may influence long segments up to their next vertex.
        # Identify all junctions of this ring once on press, never during a drag.
        contacts, pins, shared, other_owner = [], [], None, None
        for k in polygons.query(world_ring):
            if k == i:
                continue
            other = result.features[k]['geometry']
            for m, outline in enumerate([other.exterior,*other.interiors]):
                neighbor_line = pixel_line(outline)
                if not ring.intersects(neighbor_line):
                    continue
                originals.append(np.asarray(outline.coords))
                contact = ring.intersection(neighbor_line)
                pins.extend(parts(contact,'Point'))
                lines = list(parts(contact,'LineString'))
                if not lines:
                    continue
                merged = linemerge(lines)
                for line in parts(merged,'LineString'):
                    contacts.append(line)
                    if not line.is_ring:
                        pins.extend([Point(line.coords[0]),Point(line.coords[-1])])
                    if line.distance(point)<1e-6:
                        shared,other_owner=line,(int(k),m)
        if shared is None:
            free = ring.difference(unary_union(contacts)) if contacts else LineString(ring.coords)
            lines = list(parts(free,'LineString'))
            if not lines:
                return None
            shared = min(parts(linemerge(lines),'LineString'),key=lambda line: line.distance(point))
        path = shared
        if path.distance(point)>1e-6 or path.length<1e-5:
            return None
        owners = [(i,j)] + ([other_owner] if other_owner else [])
        coords = np.asarray(path.coords)
        # Keep exact source coordinates for original nodes and junctions. A
        # rotated affine can represent collinear intermediate points a few ulps apart.
        to_world = result.raster.transform
        source = np.vstack(originals)
        sx,sy = self.inverse * (source[:,0],source[:,1])
        keys = np.rint(np.column_stack((sx,sy))*1e8).astype(np.int64)
        exact = dict(zip(map(tuple,keys),source))
        world = np.asarray([exact.get(tuple(np.rint(pt*1e8).astype(np.int64)),to_world * tuple(pt)) for pt in coords])
        s = distances(coords)
        length = s[-1]
        center = path.project(point)
        if single:
            vertex = int(np.argmin(np.linalg.norm(coords-np.array(point.coords[0]),axis=1)))
            if abs(s[vertex]-center)<=tolerance:
                center=s[vertex]
        closed = path.is_ring
        stops = [path.project(pin) for pin in pins if pin.distance(path)<1e-6]
        if not closed:
            stops.extend([0.,length])
        if closed:
            stops = [pos+shift for pos in stops for shift in (-length,0.,length)]
        if any(abs(pos-center)<1e-6 for pos in stops):
            raise ValueError('Стык закреплён. Потяните линию чуть дальше от него.')
        if closed:
            s=np.r_[s[:-1]-length,s,s[1:]+length]
            coords=np.vstack((coords[:-1],coords,coords[1:]))
            world=np.vstack((world[:-1],world,world[1:]))
        if single:
            left=s[s<center-1e-6][-1]
            right=s[s>center+1e-6][0]
        else:
            reach=min(radius,.4*length) if closed else radius
            left,right=center-reach,center+reach
        left=max([left]+[pos for pos in stops if pos<center])
        right=min([right]+[pos for pos in stops if pos>center])
        if min(center-left,right-center)<1e-6:
            return None
        knots=[left,center,right]
        if not single:
            knots.extend(np.linspace(left,center,5))
            knots.extend(np.linspace(center,right,5))
        nodes=np.unique(np.r_[knots,s[(s>left)&(s<right)]])
        values=np.column_stack([np.interp(nodes,s,world[:,d]) for d in range(2)])
        pixels=np.column_stack([np.interp(nodes,s,coords[:,d]) for d in range(2)])
        t=np.where(nodes<center,(center-nodes)/(center-left),(nodes-center)/(right-center))
        weights=(1-t)**2*(1+2*t) if not single else (abs(nodes-center)<1e-8).astype(float)
        return Drag(owners,values,pixels,weights,right-left)


def replace_arc(ring, drag, moved, inverse):
    world=np.asarray(ring.coords)
    pixel_ring=transform_geometry(ring,inverse)
    s=distances(np.asarray(pixel_ring.coords))
    start,end=(pixel_ring.project(Point(p)) for p in drag.pixels[[0,-1]])
    mid=Point(LineString(drag.pixels).interpolate(.5,normalized=True))
    forward=(end-start)%s[-1]
    if (pixel_ring.project(mid)-start)%s[-1] > forward:
        start,end=end,start
        moved=moved[::-1]
    if abs((end-start)%s[-1]-drag.length)>1e-5:
        raise ValueError('Контур изменился. Выберите участок ещё раз.')
    offset=(s[:-1]-end)%s[-1]
    keep=np.flatnonzero((offset>1e-7)&(offset<(start-end)%s[-1]-1e-7))
    return np.vstack((moved,world[keep[np.argsort(offset[keep])]]))


def apply_drag(result, drag, dx, dy):
    if not np.isfinite((dx,dy)).all():
        raise ValueError('Некорректное смещение.')
    transform=result.raster.transform
    delta=np.array([transform.a*dx+transform.b*dy,transform.d*dx+transform.e*dy])
    moved=drag.world+drag.weights[:,None]*delta
    changed={}
    for i,j in drag.owners:
        old=result.features[i]['geometry']
        rings=[old.exterior,*old.interiors]
        rings[j]=replace_arc(rings[j],drag,moved,~transform)
        polygon=Polygon(rings[0],rings[1:])
        if not polygon.is_valid or polygon.area<.25*abs(transform.determinant):
            raise ValueError('Граница пересекает себя или схлопывает полигон. Уменьшите сдвиг.')
        changed[i]=polygon
    tol=abs(transform.determinant)*1e-6
    after=unary_union(list(changed.values()))
    if len(changed)==2:
        before=unary_union([result.features[i]['geometry'] for i in changed])
        if after.symmetric_difference(before).area>tol or sum(g.area for g in changed.values())-after.area>tol:
            raise ValueError('Сдвиг создаёт щель или наложение. Уменьшите смещение.')
    else:
        if transform_geometry(after,~transform).difference(_valid_footprint(result.raster)).area>1e-6:
            raise ValueError('Новая граница выходит за карту или попадает в область без данных.')
        tree=STRtree([f['geometry'] for f in result.features])
        nearby=tree.query(after)
        # Imported layers may contain tiny numeric discrepancies on coincident
        # edges. If a neighbouring arc could not be paired, refuse a one-sided edit.
        original_arc = LineString(drag.pixels)
        for k in nearby:
            if k in changed:
                continue
            neighbor = result.features[k]['geometry']
            if after.intersection(neighbor).area>tol:
                raise ValueError('Граница заходит на соседний полигон. Уменьшите смещение.')
        envelope = transform_geometry(original_arc.buffer(1e-6).envelope, transform)
        for k in tree.query(envelope):
            if k not in changed:
                boundary = transform_geometry(result.features[k]['geometry'].boundary,~transform)
                if original_arc.intersection(boundary.buffer(1e-6)).length > 1e-3:
                    raise ValueError('Не удалось согласовать общую линию с соседом.')
    features=list(result.features)
    for i,polygon in changed.items():
        features[i]=dict(geometry=polygon,properties=dict(features[i]['properties'],origin='manual_boundary',review='edited'))
    return _result_from_features(result.raster,features,result.colors,result.fields)
