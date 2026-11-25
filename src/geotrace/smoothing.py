"""Smooth a shared boundary network, with fixed junctions and checked coverage."""
import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.ops import unary_union, polygonize
from shapely.strtree import STRtree

MODES=('gentle','smooth','simplify')


def _curve(line,pixel,mode,strength,factor):
    limit=pixel*({'gentle':.15+.7*strength/100,'smooth':.2+1.4*strength/100,'simplify':.15+2*strength/100}[mode])*factor
    if mode=='simplify':return line.simplify(limit,preserve_topology=False),limit
    closed=line.is_ring
    coords=np.array(line.coords,dtype=float)
    iterations=1 if mode=='gentle' else 3
    for _ in range(iterations):
        points=coords[:-1] if closed else coords
        if len(points)<3:break
        previous=np.roll(points,1,axis=0);following=np.roll(points,-1,axis=0)
        incoming=previous-points;outgoing=following-points
        a=np.linalg.norm(incoming,axis=1);b=np.linalg.norm(outgoing,axis=1)
        # Long, pronounced corners are structural. Pixel-sized staircase corners
        # are allowed to round; junction endpoints are never changed.
        cosine=np.sum(-incoming*outgoing,axis=1)/np.maximum(a*b,1e-30)
        pinned=(a>3*pixel)&(b>3*pixel)&(cosine<.7071)
        cap=limit/(iterations*1.05)
        t=np.minimum(.22*strength/100+.06,cap/np.maximum(a,1e-30))
        u=np.minimum(.22*strength/100+.06,cap/np.maximum(b,1e-30))
        left=points+incoming*t[:,None];right=points+outgoing*u[:,None]
        output=[]
        for i in range(len(points)):
            if (not closed and i in (0,len(points)-1)) or pinned[i]:output.append(points[i])
            else:output.extend((left[i],right[i]))
        coords=np.asarray(output)
        if closed:coords=np.vstack((coords,coords[0]))
    candidate=LineString(coords).simplify(pixel*.025,preserve_topology=False)
    return candidate,limit


def _network_smooth(polygons,pixel=1.,mode='gentle',strength=50,selected=None,check=None,pixels=None):
    if mode not in MODES or not np.isfinite(strength) or not 0<=strength<=100:
        raise ValueError('Выберите тип сглаживания и силу от 0 до 100.')
    polygons=list(polygons)
    info=dict(changed=0,arcs=0,vertices_before=int(shapely.get_num_coordinates(polygons).sum()) if polygons else 0,
              vertices_after=0,max_shift_pixels=0.,limited=False)
    info['vertices_after']=info['vertices_before']
    if not polygons or strength==0:return polygons,info
    if not np.isfinite(pixel) or pixel<=0:raise ValueError('Некорректный размер пикселя сглаживания.')
    from .document import line_parts
    if not shapely.is_valid(polygons).all():raise ValueError('Сначала исправьте некорректную геометрию.')
    union=unary_union(polygons);outside=union.boundary
    if abs(sum(p.area for p in polygons)-union.area)>max(pixel*pixel*1e-7,union.area*1e-11):
        info['limited']=True;return polygons,info
    boundaries=[p.boundary for p in polygons];tree=STRtree(polygons)
    arcs=list(line_parts(shapely.line_merge(unary_union(boundaries))))
    candidates=[]
    wanted=set(selected) if selected is not None else None
    for n,arc in enumerate(arcs):
        if check and n%100==0:check()
        owners=[int(i) for i in tree.query(arc,predicate='intersects') if boundaries[int(i)].intersection(arc).length>=arc.length*(1-1e-8)]
        editable=len(owners)==2 and outside.intersection(arc).length<arc.length*1e-8
        if wanted is not None:editable &= bool(wanted.intersection(owners))
        local=min((pixels[i] for i in owners),default=pixel) if pixels is not None else pixel
        candidates.append((arc,editable,local))
    if not any(item[1] for item in candidates):return polygons,info
    original_area=shapely.area(polygons);holes=shapely.get_num_interior_rings(polygons)
    for factor in (1.,.5,.25):
        if check:check()
        lines=[];limits=np.full(len(polygons),pixel*.001);count=0
        for arc,editable,local in candidates:
            if editable:
                curved,limit=_curve(arc,local,mode,strength,factor)
                lines.append(curved);count+=not curved.equals(arc)
                for i in tree.query(arc,predicate='intersects'):limits[int(i)]=max(limits[int(i)],limit)
            else:lines.append(arc)
        if not count:return polygons,info
        faces=list(polygonize(unary_union(lines)))
        assigned={};bad=False
        for face in faces:
            indices=tree.query(face,predicate='intersects')
            overlaps=[(face.intersection(polygons[int(i)]).area,int(i)) for i in indices]
            if not overlaps:continue
            area,index=max(overlaps)
            if area<face.area*.5:continue # an unchanged exterior hole, not an object
            if index in assigned:bad=True;break
            assigned[index]=face
        if bad or len(assigned)!=len(polygons):info['reason']='face_count';continue
        output=[assigned[i] for i in range(len(polygons))]
        if not shapely.is_valid(output).all():info['reason']='invalid_geometry';continue
        if hasattr(shapely,'coverage_is_valid') and not shapely.coverage_is_valid(output):info['reason']='coverage';continue
        if not np.array_equal(shapely.get_num_interior_rings(output),holes):info['reason']='holes';continue
        displacement=shapely.hausdorff_distance(boundaries,shapely.boundary(output))
        if np.any(displacement>limits+pixel*1e-7):info['reason']='displacement';continue
        if np.any(abs(shapely.area(output)-original_area)>np.maximum(pixel*pixel*.2,original_area*.025)):info['reason']='area';continue
        after=unary_union(output)
        if after.symmetric_difference(union).area>max(pixel*pixel*1e-7,union.area*1e-11):info['reason']='footprint';continue
        if abs(sum(p.area for p in output)-after.area)>max(pixel*pixel*1e-7,union.area*1e-11):info['reason']='overlap';continue
        output=[old if new.equals(old) else new for old,new in zip(polygons,output)]
        info.update(changed=sum(a is not b for a,b in zip(polygons,output)),arcs=count,
            vertices_after=int(shapely.get_num_coordinates(output).sum()),max_shift_pixels=float(displacement.max()/pixel),limited=factor<1)
        return output,info
    info['limited']=True
    return polygons,info


def smooth_partition(polygons,pixel=1.,mode='gentle',strength=50,selected=None,check=None,pixels=None):
    original=list(polygons)
    output,info=_network_smooth(original,pixel,mode,strength,selected,check,pixels)
    if info['changed'] or not info['limited'] or len(original)<3:return output,info
    # Old, independently reprojected objects may have machine-width cracks or
    # different edge nodes. One difficult contact must not disable the whole map.
    # Work on neighbour pairs; their outer perimeter stays fixed, so all third
    # neighbours and the project footprint are retained.
    tree=STRtree(original);pairs=set();wanted=set(selected) if selected is not None else None
    for i,a in enumerate(original):
        for index in tree.query(a,predicate='intersects'):
            j=int(index)
            if j<=i or (wanted is not None and not {i,j}.intersection(wanted)):continue
            local=min(pixels[i],pixels[j]) if pixels is not None else pixel
            if a.boundary.intersection(original[j].boundary).length>local*.8:pairs.add((i,j))
    current=list(original);changed_arcs=0
    for number,(i,j) in enumerate(sorted(pairs)):
        if check and number%10==0:check()
        local=min(pixels[i],pixels[j]) if pixels is not None else pixel
        candidate,part=_network_smooth([current[i],current[j]],local,mode,strength,check=check)
        if not part['changed']:continue
        good=True
        for index,new in ((i,candidate[0]),(j,candidate[1])):
            source=original[index];own=pixels[index] if pixels is not None else pixel
            limit=own*({'gentle':.15+.7*strength/100,'smooth':.2+1.4*strength/100,'simplify':.15+2*strength/100}[mode])
            if (new.boundary.hausdorff_distance(source.boundary)>limit+own*1e-7
                or abs(new.area-source.area)>max(own*own*.2,source.area*.025)):
                good=False;break
        if good:current[i],current[j]=candidate;changed_arcs+=part['arcs']
    before=unary_union(original);after=unary_union(current)
    tolerance=max(pixel*pixel*1e-7,before.area*1e-11)
    if after.symmetric_difference(before).area>tolerance or abs(sum(g.area for g in current)-after.area)>tolerance:return original,info
    changed=sum(not a.equals(b) for a,b in zip(original,current))
    current=[a if a.equals(b) else b for a,b in zip(original,current)]
    info.update(changed=changed,arcs=changed_arcs,vertices_after=int(shapely.get_num_coordinates(current).sum()),pairwise=True,
        max_shift_pixels=max((a.boundary.hausdorff_distance(b.boundary)/pixel for a,b in zip(original,current)),default=0.))
    return current,info


def smooth_result(document,result,mode='gentle',strength=50,object_ids=None,check=None):
    from .editing import _result_from_features
    from .sheet_seams import native_pixel
    from rasterio.crs import CRS
    if CRS.from_wkt(result.raster.crs_wkt)==CRS.from_epsg(3857):
        sizes={s.id:native_pixel(s) for s in document.sheets}
    else:
        sizes={s.id:max(abs(s.raster.transform.a)*s.raster.rgb.shape[1]/s.raster.source_size[0],abs(s.raster.transform.e)*s.raster.rgb.shape[0]/s.raster.source_size[1]) for s in document.sheets}
    default=min(sizes.values(),default=1.)
    per_feature=[min((sizes[s] for s in f['properties'].get('source_sheet','').split(';') if s in sizes),default=default) for f in result.features]
    selected=None if object_ids is None else {i for i,f in enumerate(result.features) if f['properties']['object_id'] in object_ids}
    if selected is not None and not selected:raise ValueError('Выберите полигоны или группу для сглаживания.')
    originals=[f['geometry'] for f in result.features]
    polygons,info=smooth_partition(originals,default,mode,strength,selected,check=check,pixels=per_feature)
    info['max_shift_pixels']=max((old.boundary.hausdorff_distance(new.boundary)/size for old,new,size in zip(originals,polygons,per_feature)),default=0.)
    features=[]
    for f,old,new in zip(result.features,originals,polygons):
        props=dict(f['properties']);geometry=f['geometry']
        if new is not old:
            geometry=new
            props.update(review='edited',origin='manual_smooth')
        features.append(dict(geometry=geometry,properties=props))
    return _result_from_features(result.raster,features,result.colors,result.fields),info


def smooth_automatic_result(result):
    """The classic colour profiles use the same automatic finishing step.

    Keep recognition metadata unreviewed. Protected work is restored afterwards
    by the caller, so automatic smoothing cannot move an accepted manual object.
    """
    from .editing import transform_geometry,_result_from_features
    if not result.features:return result
    raster=result.raster
    native=min(raster.rgb.shape[1]/raster.source_size[0],raster.rgb.shape[0]/raster.source_size[1])
    before=[transform_geometry(f['geometry'],~raster.transform) for f in result.features]
    if not shapely.is_valid(before).all():return result
    after,_=smooth_partition(before,native)
    features=[dict(geometry=f['geometry'] if a is b else transform_geometry(b,raster.transform),properties=dict(f['properties']))
              for f,a,b in zip(result.features,before,after)]
    return _result_from_features(raster,features,result.colors,result.fields)
