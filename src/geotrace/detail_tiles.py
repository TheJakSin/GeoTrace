"""Bounded, world-aligned raster tiles retained while the camera moves."""
from collections import OrderedDict
import math
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union

from .cartography import map_image
from .sheet_seams import native_pixel

TILE=512


def render_tile(document,bounds,path):
    # A one-pixel gutter supplies consistent resampling at neighbouring edges.
    step=(bounds[2]-bounds[0])/TILE
    padded=(bounds[0]-step,bounds[1]-step,bounds[2]+step,bounds[3]+step)
    image=map_image(document,padded,TILE+2,TILE+2,detailed=True).crop((1,1,TILE+1,TILE+1))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);image.save(path)
    return path.stat().st_size


class DetailTiles:
    def __init__(self,pool,folder,budget=96*1024**2,max_tiles=192,loader=render_tile):
        self.pool=pool;self.folder=Path(folder);self.budget=budget;self.max_tiles=max_tiles;self.loader=loader
        self.cache=OrderedDict();self.bytes=0;self.futures={};self.plan=[];self.generation=0;self.signature=None
        self.view=None;self.document=None;self.raster=None;self.epoch=0;self.errors=[]

    def reset(self,document,raster):
        signature=(tuple((s.id,id(s.raster),s.visible) for s in document.sheets),
            document.area.wkb if document.area is not None else None,document.settings.get('smooth_scan_seams',True))
        self.document=document;self.raster=raster
        if signature==self.signature:return False
        self.signature=signature;self.generation+=1;self.plan=[];self.view=None
        for row in self.cache.values():Path(row['path']).unlink(missing_ok=True)
        self.cache.clear();self.bytes=0;self.epoch+=1
        self.domain=document.area if document.area is not None else unary_union([s.footprint() for s in document.sheets])
        self.minimum=min((native_pixel(s)/2 for s in document.sheets if s.visible),default=1.)
        return True

    def request(self,bounds,width,height):
        if self.raster is None:return
        if not all(math.isfinite(v) for v in (*bounds,width,height)) or min(width,height)<=0:return
        desired=max((bounds[2]-bounds[0])/width,(bounds[3]-bounds[1])/height,self.minimum)
        base=max(abs(self.raster.transform.a),abs(self.raster.transform.e))
        level=max(-6,min(12,math.ceil(math.log2(base/desired))))
        resolution=base/(2**level);span=TILE*resolution
        # At least a tile around the screen is read ahead. Keep old levels as a
        # background until new detail is ready; exact camera positions are not keys.
        view=box(*bounds);padded=view.buffer(span,cap_style='square')
        x0,y0,x1,y1=padded.bounds;cx=(bounds[0]+bounds[2])/2;cy=(bounds[1]+bounds[3])/2
        plan=[]
        for col in range(math.floor(x0/span),math.ceil(x1/span)):
            for row in range(math.floor(y0/span),math.ceil(y1/span)):
                b=(col*span,row*span,(col+1)*span,(row+1)*span)
                region=box(*b)
                if not self.domain.intersects(region):continue
                key=(self.generation,level,col,row)
                priority=(0 if region.intersects(view) else 1,(b[0]+span/2-cx)**2+(b[1]+span/2-cy)**2)
                plan.append((priority,key,b))
        self.view=(bounds,padded,level)
        self.plan=[(key,b) for _,key,b in sorted(plan)[:128]]
        for key,_ in self.plan:
            if key in self.cache:self.cache.move_to_end(key)
        self.pump()

    def pump(self):
        changed=False
        for future,(key,b,path) in list(self.futures.items()):
            if not future.done():continue
            del self.futures[future]
            try:
                size=future.result()
                if key[0]!=self.generation:
                    Path(path).unlink(missing_ok=True);continue
                self.cache[key]=dict(path=str(path),bounds=b,level=key[1],size=size)
                self.bytes+=size;changed=True
            except Exception as exc:
                try:Path(path).unlink(missing_ok=True)
                except OSError:pass
                self.errors.append(str(exc));self.errors=self.errors[-10:]
                # Don't spin on an unreadable tile until the next camera request.
                self.plan=[item for item in self.plan if item[0]!=key]
        protected={key for key,_ in self.plan[:64]}
        while self.bytes>self.budget or len(self.cache)>self.max_tiles:
            victim=next((k for k in self.cache if k not in protected),next(iter(self.cache),None))
            if victim is None:break
            row=self.cache.pop(victim);self.bytes-=row['size'];Path(row['path']).unlink(missing_ok=True);changed=True
        in_flight={item[0] for item in self.futures.values()}
        for key,b in self.plan:
            if len(self.futures)>=2:break
            if key in self.cache or key in in_flight:continue
            path=self.folder/('-'.join(map(str,key))+'.png')
            future=self.pool.submit(self.loader,self.document,b,path)
            self.futures[future]=(key,b,path);in_flight.add(key)
        if changed:self.epoch+=1
        return changed

    def rows(self):
        if self.view is None:return []
        bounds,region,level=self.view;inverse=~self.raster.transform
        rows=[]
        for key,row in self.cache.items():
            if not region.intersects(box(*row['bounds'])):continue
            x0,y0,x1,y1=row['bounds'];x,y=inverse*(x0,y1);right,bottom=inverse*(x1,y0)
            rows.append(dict(key=':'.join(map(str,key)),url=Path(row['path']).as_uri(),x=x,y=y,w=right-x,h=bottom-y,level=row['level']))
        # Current-level tiles must never be displaced by many cached fragments
        # from an older, closer zoom. Coarser detail remains a fallback while the
        # current level loads; finer retained fragments may improve it on top.
        cx,cy=inverse*((bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2)
        def distance(row):
            return (row['x']+row['w']/2-cx)**2+(row['y']+row['h']/2-cy)**2
        wanted=sorted((r for r in rows if r['level']==level),key=distance)
        fallback=sorted((r for r in rows if r['level']<level),key=lambda r:(-r['level'],distance(r)))
        finer=sorted((r for r in rows if r['level']>level),key=lambda r:(distance(r),r['level']))
        chosen=(wanted+fallback+finer)[:128]
        return sorted(chosen,key=lambda row:(row['level'],row['key']))
