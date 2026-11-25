"""Display-only treatment of exact shared sheet edges; never move source data."""
from functools import lru_cache

import numpy as np
from scipy.ndimage import map_coordinates
from shapely import (from_wkb, intersects_xy, line_interpolate_point, line_merge,
                     line_locate_point, points, get_x, get_y)
from shapely.geometry import box
from shapely.ops import unary_union
from rasterio.features import rasterize

from .spatial import DISPLAY_CRS, transformer


@lru_cache(maxsize=128)
def _contacts(footprints):
    from .document import line_parts
    shapes = [from_wkb(wkb) for wkb in footprints]
    result = []
    for i, a in enumerate(shapes):
        for j in range(i+1, len(shapes)):
            b = shapes[j]
            # Only exact, touching domains. Overlaps follow normal sheet order;
            # gaps, NoData holes and non-adjacent maps must never be filled.
            if not a.intersects(b) or a.intersection(b).area > 0:
                continue
            for line in line_parts(line_merge(a.boundary.intersection(b.boundary))):
                if line.length:
                    result.append((i, j, line))
    return tuple(result)


def contacts(document, visible=False):
    sheets = [s for s in document.sheets if not visible or s.visible]
    return sheets, _contacts(tuple(s.footprint().wkb for s in sheets))


def native_pixel(sheet):
    r = sheet.raster
    h, w = r.valid.shape
    sw, sh = r.source_size
    x, y = r.transform*(w*.5, h*.5)
    delta = [(r.transform.a*w/max(1,sw), r.transform.d*w/max(1,sw)),
             (r.transform.b*h/max(1,sh), r.transform.e*h/max(1,sh))]
    t = transformer(r.crs_wkt, DISPLAY_CRS)
    a = np.array(t.transform(x, y))
    return max(np.linalg.norm(np.array(t.transform(x+dx,y+dy))-a) for dx,dy in delta)


def _sample(raster, x, y):
    t = transformer(DISPLAY_CRS, raster.crs_wkt)
    x, y = t.transform(x, y)
    inv = ~raster.transform
    col = inv.a*x+inv.b*y+inv.c-.5
    row = inv.d*x+inv.e*y+inv.f-.5
    coords = [row, col]
    weights = raster.valid.astype('float32')
    coverage = map_coordinates(weights, coords, order=1, mode='constant', cval=0, prefilter=False)
    rgb = np.stack([map_coordinates(raster.rgb[:,:,c].astype('float32')*weights,
                    coords, order=1, mode='constant', cval=0, prefilter=False) for c in range(3)], axis=1)
    np.divide(rgb, coverage[:,None], out=rgb, where=coverage[:,None]>0)
    return rgb, coverage>.99


def _paper_edge(sheet, line):
    """Require a bright edge-row anomaly along the scan, not just pale geology."""
    pixel=native_pixel(sheet)*sheet.raster.source_size[0]/sheet.raster.rgb.shape[1]
    if line.length<8*pixel:return False
    d=np.linspace(pixel,line.length-pixel,min(2048,max(32,int(line.length/pixel))))
    foot=line_interpolate_point(line,d)
    lo=line_interpolate_point(line,d-pixel*.1);hi=line_interpolate_point(line,d+pixel*.1)
    dx,dy=get_x(hi)-get_x(lo),get_y(hi)-get_y(lo)
    norm=np.maximum(np.hypot(dx,dy),1e-30);nx,ny=-dy/norm,dx/norm
    x,y=get_x(foot),get_y(foot)
    sign=np.where(intersects_xy(sheet.footprint(),x+nx*pixel,y+ny*pixel),1.,-1.)
    nx*=sign;ny*=sign
    edge,ve=_sample(sheet.raster,x+nx*pixel*.501,y+ny*pixel*.501)
    inner,vi=_sample(sheet.raster,x+nx*pixel*2.5,y+ny*pixel*2.5)
    valid=ve&vi
    paper=(edge.min(axis=1)>=235)&(np.ptp(edge,axis=1)<24)&(edge.min(axis=1)-inner.min(axis=1)>=12)
    return bool(valid.sum()>=8 and (paper&valid).sum()/valid.sum()>=.04)


def smooth_scan_seams(document, rgb, alpha, affine, sources=None):
    """Interpolate only two native pixels beside a shared edge, with both sides.

    This removes the white/printed edge row in a scan mosaic. It is reversible
    presentation, not geological reconstruction. Alpha, coordinates and objects
    are untouched. Samples are read from the actual sheets, independent of zoom.
    """
    if not document.settings.get('smooth_scan_seams', True):
        return
    sheets, pairs = contacts(document, visible=True)
    if not pairs:
        return
    sources = sources or {s.id:s.raster for s in sheets}
    h, w = alpha.shape
    extent = box(*(affine*(0,h)), *(affine*(w,0)))
    for ia, ib, line in pairs:
        a, b = sheets[ia], sheets[ib]
        if not (_paper_edge(a,line) or _paper_edge(b,line)):
            continue
        depth_a,depth_b=2*native_pixel(a),2*native_pixel(b)
        radius=max(depth_a,depth_b)
        pixel=radius/2
        region = line.buffer(radius, cap_style='flat').intersection(extent)
        if region.is_empty:
            continue
        # Rasterise only the contact's clipped bounding box, never a whole sheet.
        inverse = ~affine
        x0,y0,x1,y1=region.bounds
        c0,r0=inverse*(x0,y1);c1,r1=inverse*(x1,y0)
        left,top=max(0,int(np.floor(c0))),max(0,int(np.floor(r0)))
        right,bottom=min(w,int(np.ceil(c1))),min(h,int(np.ceil(r1)))
        if right<=left or bottom<=top:
            continue
        from affine import Affine
        mask=rasterize([(region,1)],out_shape=(bottom-top,right-left),
            transform=affine*Affine.translation(left,top),dtype='uint8').astype(bool)
        rows,cols=np.nonzero(mask & (alpha[top:bottom,left:right]>0))
        if not len(rows):continue
        rows+=top;cols+=left
        x=affine.a*(cols+.5)+affine.c;y=affine.e*(rows+.5)+affine.f
        distance=line_locate_point(line,points(x,y))
        foot=line_interpolate_point(line,distance)
        lo=line_interpolate_point(line,np.maximum(0,distance-pixel*.1))
        hi=line_interpolate_point(line,np.minimum(line.length,distance+pixel*.1))
        dx,dy=get_x(hi)-get_x(lo),get_y(hi)-get_y(lo)
        length=np.hypot(dx,dy)
        nx,ny=-dy/np.maximum(length,1e-30),dx/np.maximum(length,1e-30)
        fx,fy=get_x(foot),get_y(foot)
        side=np.where(intersects_xy(a.footprint(),fx+nx*depth_a,fy+ny*depth_a),1.,-1.)
        nx*=side;ny*=side
        ax,ay=fx+nx*depth_a,fy+ny*depth_a;bx,by=fx-nx*depth_b,fy-ny*depth_b
        valid=intersects_xy(a.footprint(),ax,ay)&intersects_xy(b.footprint(),bx,by)
        signed=(x-fx)*nx+(y-fy)*ny
        valid &= (signed>=-depth_b)&(signed<=depth_a)
        sa,sb=sources.get(a.id),sources.get(b.id)
        if sa is None or sb is None:continue
        ca,va=_sample(sa,ax,ay);cb,vb=_sample(sb,bx,by)
        valid &= va & vb
        mix=np.clip((signed+depth_b)/(depth_a+depth_b),0,1)
        blended=ca*mix[:,None]+cb*(1-mix[:,None])
        rgb[rows[valid],cols[valid]]=np.rint(blended[valid]).clip(0,255).astype('uint8')


def technical_boundaries(document, result):
    """Per-object shared cut to omit from normal outlines, not from selection.

    A cut is hidden only between different source sheets assigned the same class
    and exactly on their shared frame. Same-sheet contacts and other classes stay.
    """
    from shapely.strtree import STRtree
    sheets,pairs=contacts(document)
    seam_parts={}
    for a,b,line in pairs:seam_parts.setdefault(frozenset((sheets[a].id,sheets[b].id)),[]).append(line)
    seams={key:unary_union(parts) for key,parts in seam_parts.items()}
    if not seams or result is None:return {}
    hidden=set(document.settings.get('hidden_classes',[]))
    fs=result.features;tree=STRtree([f['geometry'] for f in fs]);cuts={}
    for i,f in enumerate(fs):
        p=f['properties'];source=p.get('source_sheet','');cid=p['class_id']
        if cid in hidden:continue
        for j in tree.query(f['geometry'],predicate='intersects'):
            j=int(j)
            if j<=i:continue
            q=fs[j]['properties'];other=q.get('source_sheet','')
            if cid!=q['class_id'] or source==other:continue
            seam=seams.get(frozenset((source,other)))
            if seam is None:continue
            cut=f['geometry'].boundary.intersection(fs[j]['geometry'].boundary).intersection(seam)
            if cut.length:
                cuts.setdefault(i,[]).append(cut);cuts.setdefault(j,[]).append(cut)
    return {i:unary_union(parts) for i,parts in cuts.items()}
