from dataclasses import replace

import cv2
import numpy as np
from rasterio.features import rasterize
from shapely.ops import unary_union

from geotrace import quality
from geotrace.preprocessing import scan_grain, reconstruct_grainy_scan
from test_core import sample


def noisy_sheet(seed=42):
    height,width=160,240
    clean=np.full((height,width,3),[222,196,222],np.uint8)
    clean[:,120:]=[245,245,175]
    clean[20:140,156:162]=[40,85,200]  # narrow genuine dark chromatic unit
    clean[105:135,195:225]=[55,55,55]  # broad genuine neutral dark unit
    clean[25:39,203:217]=[240,90,170]  # rare real colour, under 0.6% of the map
    rng=np.random.default_rng(seed)
    rgb=(clean.astype(float)+rng.normal(0,16,clean.shape)).clip(0,255).astype(np.uint8)
    rgb[:,79:86]=[25,25,25]           # a sustained equal-colour contact
    rgb[40:105,30:40]=[20,20,20]      # thick printed stroke, not a fill
    cv2.putText(rgb,'Cu',(6,137),cv2.FONT_HERSHEY_SIMPLEX,.8,(20,20,20),4)
    valid=np.ones((height,width),bool)
    valid[65:85,175:185]=False
    rgb[~valid]=0
    return clean,rgb,valid


def test_grain_estimate_rejects_clean_edges_print_and_nodata_and_detects_noise():
    clean,rgb,valid=noisy_sheet()
    assert scan_grain(rgb,valid)>2.5
    assert scan_grain(clean,valid)<=2.5
    ink=clean.copy();ink[:,79:86]=20;ink[40:105,30:40]=20
    assert scan_grain(ink,valid)<=2.5
    assert scan_grain(rgb,np.zeros_like(valid))==0
    assert scan_grain(rgb[:8,:8],valid[:8,:8])==0


def test_thick_print_recovery_preserves_real_colours_dark_fill_and_inputs():
    clean,rgb,valid=noisy_sheet()
    before=rgb.copy(),valid.copy()
    _,ink,restored=reconstruct_grainy_scan(rgb,valid)
    assert np.median(np.linalg.norm(restored[48:95,32:38].astype(float)-clean[48:95,32:38],axis=2))<20
    for region in (np.s_[28:132,157:161],np.s_[110:130,200:220],np.s_[28:36,206:214]):
        assert np.median(np.linalg.norm(restored[region].astype(float)-clean[region],axis=2))<25
    np.testing.assert_array_equal(rgb,before[0]);np.testing.assert_array_equal(valid,before[1])
    assert restored.dtype==rgb.dtype and restored.shape==rgb.shape
    assert np.all(ink<=valid)


def test_joint_pipeline_noise_artifacts_drop_but_contact_rare_fill_and_coverage_survive(monkeypatch):
    clean,rgb,valid=noisy_sheet()
    raster=replace(sample(),rgb=rgb,valid=valid,source_size=valid.shape[::-1])
    prepared=quality.prepare(raster)
    assert prepared.grain>2.5
    actual,_=quality.segment_prepared(prepared)
    monkeypatch.setattr(quality,'scan_grain',lambda *args:0.)
    baseline,_=quality.segment_prepared(quality.prepare(raster))
    assert len(actual.features)<len(baseline.features)
    objects=rasterize([(f['geometry'],i+1) for i,f in enumerate(actual.features)],out_shape=valid.shape,transform=raster.transform)
    assert objects[20,55]!=objects[20,105], 'The original sustained contact must remain.'
    assert actual.labels[70,159]!=actual.labels[70,145], 'Keep the narrow blue unit.'
    assert actual.labels[32,210]!=actual.labels[32,230], 'Keep the rare pink unit.'
    assert actual.labels[120,210]!=actual.labels[120,230], 'Keep the broad dark fill.'
    assert np.all(actual.labels[valid]>0) and not actual.labels[~valid].any()
    polygons=[f['geometry'] for f in actual.features];union=unary_union(polygons)
    assert all(g.is_valid for g in polygons)
    assert abs(sum(g.area for g in polygons)-union.area)<1e-9
    assert abs(union.area-valid.sum()*abs(raster.transform.determinant))<1e-9
    np.testing.assert_array_equal(raster.rgb,rgb)


def test_grain_recovery_is_cancellable_without_mutating_source():
    import pytest
    _,rgb,valid=noisy_sheet();before=rgb.copy()
    def cancel():raise quality.Cancelled('Остановлено')
    with pytest.raises(quality.Cancelled):reconstruct_grainy_scan(rgb,valid,check=cancel)
    np.testing.assert_array_equal(rgb,before)


def mixed_grain_sheet():
    """A clean majority hides a genuinely noisy region from the global median."""
    height, width = 384, 960
    clean = np.full((height, width, 3), [222, 196, 222], np.uint8)
    clean[:, 480:] = [245, 245, 175]
    clean[200:340, 740:748] = [40, 85, 200]
    clean[210:242, 820:852] = [240, 90, 170]
    clean[280:320, 840:880] = [55, 55, 55]
    rgb = clean.copy()
    rng = np.random.default_rng(42)
    noisy = rgb[:, 576:]
    rgb[:, 576:] = np.clip(noisy.astype(float) + rng.normal(0, 16, noisy.shape), 0, 255).astype(np.uint8)
    cv2.putText(rgb, 'Cu R', (600, 170), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 20, 20), 5)
    valid = np.ones((height, width), bool)
    valid[100:120, 700:720] = False
    return clean, rgb, valid


def test_regional_grain_detects_substantial_noise_behind_a_clean_majority():
    clean, rgb, valid = mixed_grain_sheet()
    from scipy import ndimage
    indices = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = rgb[tuple(indices)]
    lab = cv2.cvtColor(filled, cv2.COLOR_RGB2LAB).astype(np.float32)
    median = cv2.cvtColor(cv2.medianBlur(filled, 3), cv2.COLOR_RGB2LAB).astype(np.float32)
    broad = cv2.GaussianBlur(lab, (0, 0), 3)
    paper = (valid & (lab[:, :, 0] > 180)
             & (np.linalg.norm(cv2.Sobel(broad, cv2.CV_32F, 1, 0)/8, axis=2) < 3)
             & (np.linalg.norm(cv2.Sobel(broad, cv2.CV_32F, 0, 1)/8, axis=2) < 3))
    assert np.median(np.linalg.norm(lab[:, :, 1:] - median[:, :, 1:], axis=2)[paper]) <= 2.5
    assert scan_grain(rgb, valid) > 2.5
    assert scan_grain(clean, valid) <= 2.5
    assert scan_grain(np.rot90(rgb).copy(), np.rot90(valid).copy()) > 2.5


def test_regional_grain_ignores_a_single_damaged_corner_and_transparency():
    clean, rgb, valid = mixed_grain_sheet()
    corner = clean.copy()
    corner[:192, -192:] = rgb[:192, -192:]
    assert scan_grain(corner, valid) <= 2.5
    hidden = valid.copy()
    hidden[:, 576:] = False
    assert scan_grain(rgb, hidden) <= 2.5
    hidden[::40, 576:] = True
    assert scan_grain(rgb, hidden) <= 2.5


def test_regional_recovery_keeps_rare_dark_narrow_fills_and_exact_coverage(monkeypatch):
    clean, rgb, valid = mixed_grain_sheet()
    before = rgb.copy(), valid.copy()
    raster = replace(sample(), rgb=rgb, valid=valid, source_size=valid.shape[::-1])
    prepared = quality.prepare(raster)
    assert prepared.grain > 2.5
    actual, _ = quality.segment_prepared(prepared)
    monkeypatch.setattr(quality, 'scan_grain', lambda *args: 0.)
    baseline, _ = quality.segment_prepared(quality.prepare(raster))
    assert len(actual.features) < len(baseline.features)
    for y, x, neighbour in ((270, 744, 730), (226, 836, 890), (300, 860, 890)):
        assert actual.labels[y, x] != actual.labels[y, neighbour]
    assert np.all(actual.labels[valid] > 0) and not actual.labels[~valid].any()
    shapes = [f['geometry'] for f in actual.features]
    union = unary_union(shapes)
    assert all(g.is_valid for g in shapes)
    assert abs(sum(g.area for g in shapes) - union.area) < 1e-9
    assert abs(union.area - valid.sum()*abs(raster.transform.determinant)) < 1e-9
    np.testing.assert_array_equal(rgb, before[0])
    np.testing.assert_array_equal(valid, before[1])
