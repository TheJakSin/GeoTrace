import numpy as np
from affine import Affine
from rasterio.crs import CRS

from geotrace.core import Raster
from geotrace.document import Document,Sheet
from geotrace.cartography import map_image


def test_resampling_cannot_bleed_black_nodata_into_map_colors():
    rgb=np.zeros((60,60,3),np.uint8);valid=np.zeros((60,60),bool)
    valid[10:50,10:50]=True;rgb[valid]=[230,120,80]
    raster=Raster('',rgb,valid,Affine(2,0,1e6,0,-2,7e6),CRS.from_epsg(3857).to_wkt(),'EPSG:3857',(60,60))
    doc=Document(sheets=[Sheet('a','A',raster)],active_sheet='a')
    output=np.asarray(map_image(doc,(1e6+.4,7e6-119.6,1e6+120.4,7e6+.4),73,73))
    visible=output[:,:,3]>0
    assert visible.any()
    np.testing.assert_allclose(output[:,:,:3][visible],np.broadcast_to([230,120,80],(visible.sum(),3)),atol=1)
