"""Local boundary attachment, keeping feature identities and attributes intact."""
from dataclasses import dataclass
import numpy as np
from shapely import STRtree
from shapely.geometry import Point,LineString,Polygon
from shapely.ops import substring,unary_union
from .boundary_drag import Drag,distances,replace_arc,parts
from .editing import transform_geometry,_valid_footprint,_result_from_features


def ring_arc(ring,start,end):
    line=LineString(ring.coords);length=line.length
    a,b=line.project(Point(start)),line.project(Point(end))
    if min(abs(a-b),length-abs(a-b))<1e-6:raise ValueError('Начало и конец участка должны различаться.')
    def forward(a,b):
        if b>a:return np.asarray(substring(line,a,b).coords)
        return np.vstack((np.asarray(substring(line,a,length).coords),np.asarray(substring(line,0,b).coords)[1:]))
    if (b-a)%length<=length/2:return forward(a,b)
    return forward(b,a)[::-1]


class Context:
    def __init__(self,result,blocked=()):
        self.result=result;self.inverse=~result.raster.transform
        self.tree=STRtree([f['geometry'] for f in result.features]);self.blocked=set(blocked);self._footprint=None
        self.edge_index=None
    @property
    def footprint(self):
        if self._footprint is None:self._footprint=_valid_footprint(self.result.raster)
        return self._footprint
    def ring(self,i,j=0):
        g=self.result.features[i]['geometry'];return transform_geometry([g.exterior,*g.interiors][j],self.inverse)


def span_drag(context,owner,ring,start,end,tolerance):
    outline=context.ring(owner,ring)
    if outline.distance(Point(end))>tolerance:raise ValueError('Вторую точку поставьте на той же границе выбранного полигона.')
    coords=ring_arc(outline,start,end);world=np.array([context.result.raster.transform*tuple(p) for p in coords])
    # A two-click span may end at a junction but must never pull one through.
    path=LineString(coords);world_path=LineString(world)
    owners=[(owner,ring)]
    for k in context.tree.query(world_path):
        k=int(k)
        if k==owner:continue
        other=transform_geometry(context.result.features[k]['geometry'].boundary,context.inverse)
        contact=path.intersection(other)
        if contact.length>1e-6:raise ValueError('Этот участок уже общий. Для его перемещения используйте «Тянуть».')
        for pt in parts(contact,'Point'):
            s=path.project(pt)
            if 1e-6<s<path.length-1e-6:raise ValueError('На участке есть стык нескольких границ. Выберите участок до стыка.')
    return Drag(owners,world,coords,np.ones(len(coords)),path.length)


@dataclass
class Target:
    owner:int
    ring:int
    pixels:np.ndarray
    world:np.ndarray
    score:float


def find_target(context,drag,moved,tolerance,preferred=None):
    if tolerance<=0 or not np.isfinite(tolerance):raise ValueError('Некорректная область захвата.')
    inverse=context.inverse
    pixel=np.array([inverse*tuple(p) for p in moved]);active=np.flatnonzero(drag.weights>.2)
    if len(active)<2:active=np.arange(len(pixel))
    first,last=pixel[active[0]],pixel[active[-1]];path=LineString(pixel)
    center=path.interpolate(.5,normalized=True)
    bounds=transform_geometry(path.buffer(tolerance*4).envelope,context.result.raster.transform)
    exclude={i for i,j in drag.owners};targets=[]
    for k in context.tree.query(bounds):
        k=int(k)
        if k in exclude or k in context.blocked:continue
        g=context.result.features[k]['geometry']
        # Interior rings represent real holes and islands; choose them explicitly
        # through their own exterior rather than grabbing across an unrelated hole.
        ring=context.ring(k);distance=ring.distance(center)
        capture=tolerance*(1.6 if k==preferred else 1.)
        if distance>capture:continue
        if max(ring.distance(Point(first)),ring.distance(Point(last)))>tolerance*4:continue
        try:arc=ring_arc(ring,first,last)
        except ValueError:continue
        length=LineString(arc).length
        if not .2*path.length<=length<=5*path.length:continue
        a,b=last-first,arc[-1]-arc[0]
        alignment=abs(np.dot(a,b))/max(np.linalg.norm(a)*np.linalg.norm(b),1e-30)
        if alignment<.55:continue
        score=distance+tolerance*(1-alignment)
        native=context.result.features[k]['geometry'].exterior
        t=context.result.raster.transform
        world=ring_arc(native,t*tuple(first),t*tuple(last))
        arc=np.array([context.inverse*tuple(p) for p in world])
        targets.append(Target(k,0,arc,world,score))
    if preferred is not None:
        held=next((t for t in targets if t.owner==preferred),None)
        if held:return held
    return min(targets,key=lambda t:(t.score,t.owner)) if targets else None


def moved_arc(context,drag,dx,dy):
    if not np.isfinite((dx,dy)).all():raise ValueError('Некорректное смещение.')
    t=context.result.raster.transform
    return drag.world+drag.weights[:,None]*np.array([t.a*dx+t.b*dy,t.d*dx+t.e*dy])


def _polygon(context,index,geometry):
    old=context.result.features[index]['geometry']
    if geometry.geom_type!='Polygon' or not geometry.is_valid or geometry.area<abs(context.result.raster.transform.determinant)*.25:
        raise ValueError('Соединение разрезает или удаляет полигон. Уменьшите участок правки.')
    if len(geometry.interiors)!=len(old.interiors):raise ValueError('Соединение затрагивает внутреннюю область. Выберите меньший участок.')
    for hole in old.interiors:
        if not any(Polygon(hole).equals(Polygon(r)) for r in geometry.interiors):raise ValueError('Соединение затрагивает внутреннюю область. Выберите меньший участок.')
    return geometry


def assemble(context,drag,arc,fixed=()):
    result=context.result;changed={};fixed=set(fixed);tol=abs(result.raster.transform.determinant)*1e-6
    for i,j in drag.owners:
        if i in context.blocked:raise ValueError('Группа скрыта или заблокирована.')
        old=result.features[i]['geometry'];rings=[old.exterior,*old.interiors]
        rings[j]=replace_arc(rings[j],drag,arc,context.inverse)
        changed[i]=_polygon(context,i,Polygon(rings[0],rings[1:]))
    primary=drag.owners[0][0]
    for k in fixed:
        if changed[primary].intersection(result.features[k]['geometry']).area>tol:
            changed[primary]=_polygon(context,primary,changed[primary].difference(result.features[k]['geometry']))
    for i,g in list(changed.items()):
        if transform_geometry(g,context.inverse).difference(context.footprint).area>1e-6:
            raise ValueError('Новая граница выходит за участок или попадает в область без данных.')
        for k in context.tree.query(g):
            k=int(k)
            if k in changed or k in fixed:continue
            old=result.features[k]['geometry']
            if g.intersection(old).area<=tol:continue
            if k in context.blocked:raise ValueError('Правка затрагивает скрытую или заблокированную группу.')
            changed[k]=_polygon(context,k,old.difference(g))
    # A pre-existing shared edge can change only inside its original coverage.
    if len(drag.owners)>1:
        indices=[i for i,j in drag.owners]
        before=unary_union([result.features[i]['geometry'] for i in indices]);after=unary_union([changed[i] for i in indices])
        if before.symmetric_difference(after).area>tol:raise ValueError('Правка проходит через стык. Уменьшите участок.')
    geometries=list(changed.values());covered=unary_union(geometries)
    if sum(g.area for g in geometries)-covered.area>tol:raise ValueError('Соединение создаёт наложение. Уменьшите участок.')
    changed={i:g for i,g in changed.items() if not g.equals_exact(result.features[i]['geometry'],0)}
    return changed


def node_anchor(context,changed,index,ring,pixel_arc,world_arc):
    """Use identical segment endpoints on both sides, including affine roundoff."""
    old=context.result.features[index]['geometry'];rings=[old.exterior,*old.interiors]
    if index in changed and changed[index].symmetric_difference(old).area>abs(context.result.raster.transform.determinant)*1e-6:
        raise ValueError('Концы выбранного участка пересекают соседа. Выберите меньший участок.')
    original=Drag([(index,ring)],world_arc,pixel_arc,np.ones(len(pixel_arc)),LineString(pixel_arc).length)
    rings[ring]=replace_arc(rings[ring],original,world_arc,context.inverse)
    changed[index]=_polygon(context,index,Polygon(rings[0],rings[1:]))


def validate_contacts(context,changed):
    tolerance=abs(context.result.raster.transform.determinant)*1e-6
    for i,g in changed.items():
        for j in context.tree.query(g):
            j=int(j)
            if j==i or (j in changed and j<i):continue
            other=changed.get(j,context.result.features[j]['geometry'])
            if g.intersection(other).area>tolerance:raise ValueError('Соединение создаёт наложение. Выберите меньший участок.')


def connect_drag(context,drag,dx,dy,tolerance,preferred=None):
    # Widen the moving section for a long pull, avoiding a needle-shaped tip.
    # Junctions still stop the section and an explicit single vertex stays single.
    travel=float(np.hypot(dx,dy))
    if travel*2.5>drag.length and np.count_nonzero(drag.weights>1e-3)>1:
        from .boundary_drag import EdgeIndex
        if context.edge_index is None:context.edge_index=EdgeIndex(context.result)
        center=drag.pixels[int(np.argmax(drag.weights))]
        expanded=context.edge_index.plan(*center,max(tolerance,travel/4),False,context.tree)
        if expanded is not None and set(expanded.owners)==set(drag.owners):drag=expanded
    arc=moved_arc(context,drag,dx,dy);target=find_target(context,drag,arc,tolerance,preferred)
    if target:
        arc=np.vstack((drag.world[0],target.world,drag.world[-1]))
    changed=assemble(context,drag,arc)
    if target:node_anchor(context,changed,target.owner,target.ring,target.pixels,target.world)
    validate_contacts(context,changed)
    involved=set(changed)
    if target:involved.add(target.owner)
    if len(drag.owners)==1:
        primary=drag.owners[0][0];g=changed.get(primary,context.result.features[primary]['geometry'])
        edited=LineString(arc)
        contacts=[int(k) for k in context.tree.query(g) if int(k)!=primary and g.boundary.intersection(changed.get(int(k),context.result.features[int(k)]['geometry']).boundary).intersection(edited).length>1e-6]
        involved.update(contacts)
    return dict(changed=changed,arc=arc,target=target.owner if target else None,involved=sorted(involved),snapped=bool(target))


def connect_span(context,drag,tolerance,anchor='neighbor'):
    if anchor not in ('neighbor','source'):raise ValueError('Выберите, чей контур сохранить.')
    radius=max(tolerance*3,min(drag.length*.5,tolerance*12))
    target=find_target(context,drag,drag.world,radius)
    if target is None:raise ValueError('Рядом с выбранным участком нет подходящего контура. Выберите участок ближе к соседу.')
    if anchor=='neighbor':
        arc=np.vstack((drag.world[0],target.world,drag.world[-1]))
        changed=assemble(context,drag,arc)
        node_anchor(context,changed,target.owner,target.ring,target.pixels,target.world)
    else:
        world=target.world
        reverse=Drag([(target.owner,target.ring)],world,target.pixels,np.ones(len(world)),LineString(target.pixels).length)
        arc=np.vstack((world[0],drag.world,world[-1]));changed=assemble(context,reverse,arc,fixed=[drag.owners[0][0]])
        node_anchor(context,changed,drag.owners[0][0],drag.owners[0][1],drag.pixels,drag.world)
    validate_contacts(context,changed)
    return dict(changed=changed,arc=arc,target=target.owner,involved=sorted(set(changed)|{target.owner,drag.owners[0][0]}),snapped=True)


def finish(context,plan):
    if not plan['changed']:raise ValueError('Границы уже совпадают на выбранном участке.')
    features=list(context.result.features)
    for i,g in plan['changed'].items():
        features[i]=dict(geometry=g,properties=dict(features[i]['properties'],origin='manual_boundary',review='edited'))
    return _result_from_features(context.result.raster,features,context.result.colors,context.result.fields)


def preview(context,plan):
    def coords(points):return [dict(x=float(x),y=float(y)) for x,y in points]
    areas=[]
    for i,g in plan['changed'].items():
        delta=g.symmetric_difference(context.result.features[i]['geometry'])
        for p in parts(delta,'Polygon'):
            pixel=transform_geometry(p,context.inverse).simplify(.12,preserve_topology=True)
            areas.append([coords(pixel.exterior.coords),*[coords(r.coords) for r in pixel.interiors]])
    arc=[context.inverse*tuple(p) for p in plan['arc']]
    return dict(valid=True,arc=coords(arc),areas=areas,objects=[dict(context.result.features[i]['properties']) for i in plan['involved']],snapped=plan['snapped'],target=plan['target'])
