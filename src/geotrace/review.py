"""Localise candidate edits on the final working grid, including protected geometry."""
import base64
import cv2
import numpy as np
from scipy import ndimage
from rasterio.features import rasterize
from .automatic import protected_feature


def marker(mask, x, y):
    """Encode just the local evidence. Its pixel coordinates share the map transform."""
    yy,xx=np.where(mask)
    x0,x1=int(xx.min()),int(xx.max())+1
    y0,y1=int(yy.min()),int(yy.max())+1
    selected=np.pad(mask[y0:y1,x0:x1],1)
    ink=np.zeros((*selected.shape,4),np.uint8)
    ink[ndimage.binary_dilation(selected)]=[255,255,255,150]
    ink[selected]=[138,25,221,225]  # OpenCV BGRA, raspberry on the map
    ok,png=cv2.imencode('.png',ink)
    if not ok:
        raise ValueError('Не удалось подготовить отметку спорного участка.')
    return dict(x=x+x0-1,y=y+y0-1,w=ink.shape[1],h=ink.shape[0],
                url='data:image/png;base64,'+base64.b64encode(png).decode('ascii'))


def contact_mask(objects, eligible, colors, x, y):
    # Find the nearest surviving same-class pair, then trace only that pair.
    x0,y0=max(0,x-24),max(0,y-24)
    tile=objects[y0:y+25,x0:x+25]
    best=None
    edges=[]
    for head,tail in ((tile[:-1],tile[1:]),(tile[:,:-1],tile[:,1:])):
        yy,xx=np.where((head!=tail)&eligible[head]&eligible[tail]&(colors[head]==colors[tail]))
        if not len(xx):
            continue
        a,b=head[yy,xx],tail[yy,xx]
        distance=(xx+x0-x)**2+(yy+y0-y)**2
        k=int(np.argmin(distance))
        pair=(int(distance[k]),int(min(a[k],b[k])),int(max(a[k],b[k])))
        if best is None or pair<best:
            best=pair
        edges.append((xx,yy,np.minimum(a,b),np.maximum(a,b)))
    if best is None or best[0]>18:
        return None
    mask=np.zeros(tile.shape,bool)
    for xx,yy,a,b in edges:
        keep=(a==best[1])&(b==best[2])
        mask[yy[keep],xx[keep]]=True
    _, components=cv2.connectedComponents(mask.astype(np.uint8),connectivity=8)
    yy,xx=np.where(mask)
    nearest=np.argmin((xx+x0-x)**2+(yy+y0-y)**2)
    return components==components[yy[nearest],xx[nearest]],x0,y0


def review_sites(result, evidence, margin, contact_checks=()):
    if not result.features:
        return []
    objects = rasterize([(f['geometry'],i) for i,f in enumerate(result.features,1)],
                        out_shape=result.labels.shape,transform=result.raster.transform,dtype='int32')
    sizes = np.bincount(objects.ravel(),minlength=len(result.features)+1)
    eligible = np.array([False]+[not protected_feature(f,result.fields) for f in result.features])
    colors = np.array([0]+[f['properties']['class_id'] for f in result.features])
    edge = np.zeros(objects.shape,bool)
    weak = np.zeros_like(edge)
    fragments = np.zeros_like(edge)
    for a,b,contrast,other,cut,low,small in (
            (objects[:-1],objects[1:],evidence[:-1],evidence[1:],edge[:-1],weak[:-1],fragments[:-1]),
            (objects[:,:-1],objects[:,1:],evidence[:,:-1],evidence[:,1:],edge[:,:-1],weak[:,:-1],fragments[:,:-1])):
        shared = (a!=b) & eligible[a] & eligible[b]
        cut |= shared
        low |= shared & (np.maximum(contrast,other)<.24)
        small |= shared & (colors[a]==colors[b]) & (np.minimum(sizes[a],sizes[b])<128)
    # Long strokes well inside an object can mark a missed contact. Keep them as
    # review suggestions because text, faults and hatch strokes can look similar.
    inside = eligible[objects] & ~ndimage.binary_dilation(edge, iterations=3)
    inside &= ndimage.binary_erosion(objects > 0, iterations=3)
    ridges = (inside & (evidence >= .65)).astype(np.uint8)
    candidates = []
    h,w = edge.shape
    # Validate against the final vector layer: simplification, refinement and
    # protected edits can shift or remove the original contact.
    for x,y in contact_checks:
        trace=contact_mask(objects,eligible,colors,x,y)
        if trace is not None:
            bounds=[max(0,x-52),max(0,y-52),min(w,x+52),min(h,y+52)]
            candidates.append((60,x,y,dict(bounds=bounds,reason='Похожие заливки, спорная печатная текстура',tool=0),trace))
    for y in range(0,h,64):
        for x in range(0,w,64):
            roi = np.s_[y:y+64,x:x+64]
            length = int(edge[roi].sum())
            counts, labels, stats, _ = cv2.connectedComponentsWithStats(ridges[roi], connectivity=8)
            strokes = stats[1:counts]
            spans = np.maximum(strokes[:,cv2.CC_STAT_WIDTH], strokes[:,cv2.CC_STAT_HEIGHT])
            narrow = strokes[:,cv2.CC_STAT_AREA] <= 5*spans
            stroke = int(spans[narrow].max(initial=0))
            density = int(fragments[roi].sum())
            unsupported = int(weak[roi].sum())
            ambiguous = int((edge[roi] & (margin[roi]<3)).sum())
            def suggest(mask, reason, tool, score):
                # A tile may contain several unrelated contacts. Highlight the
                # strongest connected trace and centre the view on that trace.
                count,components,stats,centers=cv2.connectedComponentsWithStats(mask.astype(np.uint8),connectivity=8)
                if count<2:
                    return
                strongest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
                cx,cy=centers[strongest].round().astype(int)
                cx,cy=int(cx)+x,int(cy)+y
                bounds=[max(0,cx-52),max(0,cy-52),min(w,cx+52),min(h,cy+52)]
                site=dict(bounds=bounds,reason=reason,tool=tool)
                candidates.append((score,cx,cy,site,(components==strongest,x,y)))
            if length >= 12 and density >= 16:
                suggest(fragments[roi],'Мелкие соседние полигоны одной группы',0,density)
            if length >= 12 and unsupported >= 12 and unsupported >= .35*length:
                suggest(weak[roi],'Слабая граница между полигонами',2,unsupported+.2*ambiguous)
            if stroke >= 32:
                marks=labels==(int(np.argmax(np.where(narrow,spans,0)))+1)
                suggest(marks,'Внутри полигона проходит возможная граница',1,stroke)
    sites = []
    for _,x,y,site,trace in sorted(candidates,key=lambda p:-p[0]):
        if all(prior['reason']!=site['reason'] or abs(x-a)>=96 or abs(y-b)>=96 for a,b,prior in sites):
            site['marker']=marker(*trace)
            sites.append((x,y,site))
    return [site for _,_,site in sites]
