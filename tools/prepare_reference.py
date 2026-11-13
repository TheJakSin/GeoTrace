from pathlib import Path
import fiona,numpy as np,json
from scipy import ndimage
from rasterio.features import rasterize
from shapely.geometry import shape
from geotrace.core import read_raster
from geotrace.prompts import roi_at
import argparse
parser=argparse.ArgumentParser(description='Prepare fixed diagnostic cases from the supplied example; prompts are reference-derived.')
parser.add_argument('--example',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
root=args.example
args.out.mkdir(parents=True,exist_ok=True)
r=read_raster(root/'Растр/Геологическая карта_clip_cut_buffer800.tif')
with fiona.open(root/'Вектор/gpkg/geolmap_edit.gpkg') as src:fs=list(src)
fs=[f for f in fs if not shape(f['geometry']).is_empty]
labels=rasterize([(f['geometry'],i+1) for i,f in enumerate(fs)],out_shape=r.valid.shape,transform=r.transform)
cases=[]
for code in [6,3,8,1]:
 candidates=[(int((labels==i+1).sum()),i+1,f) for i,f in enumerate(fs) if f['properties']['code']==code]
 _,ident,f=max(candidates,key=lambda v:v[0]); target=(labels==ident)&r.valid
 dist=ndimage.distance_transform_edt(target); y,x=np.unravel_index(dist.argmax(),dist.shape)
 roi=roi_at(r,int(x),int(y),512); x0,y0,x1,y1=roi
 points=[[int(x),int(y),1]]
 bg=(labels[y0:y1,x0:x1]!=ident)&(labels[y0:y1,x0:x1]>0)&r.valid[y0:y1,x0:x1]
 bg[:8]=False; bg[-8:]=False; bg[:,:8]=False; bg[:,-8:]=False
 # Choose two spatially-separated interior background points, from reference (oracle prompts).
 for _ in range(2):
  bd=ndimage.distance_transform_edt(bg); ny,nx=np.unravel_index(bd.argmax(),bd.shape)
  points.append([int(nx+x0),int(ny+y0),0])
  yy,xx=np.indices(bg.shape); bg &= ((xx-nx)**2+(yy-ny)**2)>100**2
 case={'name':str(f['properties']['indeks']),'id':ident,'roi':roi,'points':points}
 cases.append(case)
np.savez_compressed(args.out/'data.npz',rgb=r.rgb,valid=r.valid,reference=labels,transform=list(r.transform)[:6])
(args.out/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
print(json.dumps(cases,ensure_ascii=False))
