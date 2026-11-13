import numpy as np
import pytest
import fiona
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import unary_union

from geotrace.core import apply_proposal, segment, export_gpkg
from geotrace.prompts import Proposal, propose, roi_at
from test_core import sample


def test_hints_keep_foreground_exclude_background_and_nodata():
    r = sample()
    roi = roi_at(r, 70, 30)
    output = propose(r, roi, [(70,30,1), (10,10,0)])
    assert output.mask[30,70]
    assert not output.mask[10,10]
    assert not (output.mask & ~r.valid).any()
    assert output.mask[:,50:].mean() > .95
    assert output.mask[:,:45].mean() < .02


@pytest.mark.parametrize('points', [[], [(10,10,0)], [(500,500,1)], [(25,25,1)], [(10,10,1),(10,10,0)]])
def test_invalid_or_conflicting_hints_rejected(points):
    r = sample()
    with pytest.raises(ValueError):
        propose(r, (0,0,100,60), points)


def test_acceptance_splits_only_touched_object_and_preserves_attributes(tmp_path):
    r = sample()
    result = segment(r, 2, 10)
    original_labels = result.labels.copy()
    original_geoms = [f['geometry'] for f in result.features]
    for f in result.features:
        f['properties']['unit_name'] = 'Исходная свита'
    mask = np.zeros(r.valid.shape, bool)
    mask[:,70:80] = True
    proposal = Proposal(mask,(0,0,100,60),'color',True)
    updated = apply_proposal(r,result,proposal)
    # The vertical stripe splits the right-hand original polygon into two remnants.
    assert len(updated.features) == 4
    assert len({f['properties']['object_id'] for f in updated.features}) == 4
    assert all(f['properties']['unit_name']=='Исходная свита'
               for f in updated.features if f['properties']['review'] != 'accepted')
    np.testing.assert_array_equal(result.labels, original_labels)
    assert all(f['geometry'].equals(g) for f,g in zip(result.features,original_geoms))
    np.testing.assert_array_equal(updated.labels[~mask],original_labels[~mask])
    geoms = [f['geometry'] for f in updated.features]
    union = unary_union(geoms)
    assert all(g.is_valid for g in geoms)
    assert sum(g.area for g in geoms) == pytest.approx(union.area)
    assert union.area == pytest.approx(unary_union(original_geoms).area)
    output = tmp_path/'accepted.gpkg'
    export_gpkg(updated,output)
    with fiona.open(output) as src:
        records = list(src)
    assert any(f['properties']['origin']=='prompt_color' for f in records)
    assert all(shape(f['geometry']).is_valid for f in records)
    with pytest.raises(ValueError,match='уже принятую'):
        apply_proposal(r,updated,proposal)


def test_acceptance_without_full_segmentation_exports_only_selected_area(tmp_path):
    r = sample()
    mask = np.zeros(r.valid.shape,bool)
    mask[5:15,5:15] = True
    mask[40:50,60:70] = True
    result = apply_proposal(r,None,Proposal(mask,(0,0,100,60),'color',False))
    assert len(result.features)==2
    reconstructed = rasterize([(f['geometry'],1) for f in result.features],out_shape=mask.shape,transform=r.transform)
    np.testing.assert_array_equal(reconstructed.astype(bool),mask)
    assert np.all(result.labels[~mask]==0)


def test_roi_is_bounded_and_outside_pixels_unchanged():
    r = sample()
    # A small ROI still accepts explicit valid bounds; automatic UI frames have min 128 px.
    roi=(50,0,100,40)
    proposal=propose(r,roi,[(70,20,1)])
    assert not proposal.mask[:,:50].any()
    assert not proposal.mask[40:].any()
    assert proposal.touches_edge


def test_edited_attributes_protect_geometry():
    r=sample()
    result=segment(r,2,10)
    for f in result.features:
        f['properties']['review']='edited'
    mask=np.zeros(r.valid.shape,bool); mask[1:10,1:10]=True
    with pytest.raises(ValueError,match='отредактированную'):
        apply_proposal(r,result,Proposal(mask,(0,0,100,60),'color',False))
