import numpy as np
import pytest
from geotrace.palette import sample_fill, split_labels
from geotrace.review import review_sites
from geotrace.core import segment
from test_core import sample


@pytest.mark.parametrize('x',[4,16])
def test_new_group_can_pick_either_shade(x):
    labels = np.ones((12,30),np.uint8)
    labels[:,20:] = 2
    colors = np.full((*labels.shape,3),100,np.float32)
    colors[:,12:20,1] = 108
    original = labels.copy()
    mask = split_labels(colors,labels,sample_fill(colors,labels,x,6,3))
    assert mask[6,x] == 3
    assert mask[6,16 if x==4 else 4] == 1
    assert (mask[:,20:] == 2).all()
    np.testing.assert_array_equal(labels,original)


def test_no_group_for_identical_shade():
    with pytest.raises(ValueError):
        sample_fill(np.zeros((10,10,3)),np.ones((10,10)),5,5,2)


def test_review_suggests_dividing_on_long_interior_stroke():
    result = segment(sample(),2,10)
    strength = np.zeros(result.labels.shape)
    result.features = result.features[:1]
    point = result.features[0]['geometry'].representative_point()
    x,y = (int(v) for v in ~result.raster.transform*(point.x,point.y))
    strength[y-18:y+18,x] = 1.
    sites = review_sites(result,strength,np.full_like(strength,10))
    assert any(site['tool']==1 for site in sites)
    result.features[0]['properties']['review']='edited'
    assert not review_sites(result,strength,np.full_like(strength,10))


def test_missing_boundary_marker_follows_detected_ink():
    import base64
    import cv2
    result=segment(sample(),2,10)
    result.features=result.features[:1]
    point=result.features[0]['geometry'].representative_point()
    x,y=(int(v) for v in ~result.raster.transform*(point.x,point.y))
    strength=np.zeros(result.labels.shape)
    strength[y-18:y+18,x]=1
    site=review_sites(result,strength,np.full_like(strength,10))[0]
    mark=site['marker']
    raw=base64.b64decode(mark['url'].split(',')[1])
    rgba=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_UNCHANGED)
    ink_y,ink_x=np.where(rgba[:,:,3]==225)
    assert len(ink_x)>=32
    assert (ink_x+mark['x']==x).all()
    assert (strength[ink_y+mark['y'],ink_x+mark['x']]==1).all()
