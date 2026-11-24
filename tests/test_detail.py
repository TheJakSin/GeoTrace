from dataclasses import replace

import numpy as np
import rasterio
from affine import Affine
from shapely.geometry import box

from geotrace.core import read_raster
from geotrace.detail import source_window
from geotrace.region import set_area


def test_detail_reads_only_source_window_and_respects_map_frame(tmp_path):
    path=tmp_path/'source.tif'
    y,x=np.indices((256,4096));rgb=np.stack(((x%2)*255,y%256,np.full_like(x,90))).astype('uint8')
    with rasterio.open(path,'w',driver='GTiff',width=4096,height=256,count=3,dtype='uint8',crs='EPSG:3857',transform=Affine(2,0,1e6,0,-2,7e6)) as dst:
        dst.write(rgb)
    working=read_raster(path)
    bounds=(1e6+2000,7e6-400,1e6+2400,7e6-100)
    fine=source_window(working,bounds,side=1000)
    assert fine.rgb.shape[1]<210 and fine.rgb.shape[0]<160
    assert abs(fine.transform.a)==2 and abs(working.transform.a)>2
    assert fine.rgb[:,:,0].max()==255 and fine.rgb[:,:,0].min()==0
    framed=set_area(working,box(1e6+2100,7e6-500,1e6+2300,7e6))
    clipped=source_window(framed,bounds)
    assert clipped.valid.any() and not clipped.valid.all()
    changed=replace(working,rgb=working.rgb.copy())
    changed.rgb[0,0]=[1,2,3]
    assert source_window(changed,bounds) is changed, 'Never substitute an edited snapshot with a different source.'
