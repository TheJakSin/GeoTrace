from dataclasses import replace
import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.features import rasterize
from shapely.ops import unary_union
from geotrace.core import Raster,read_raster
from geotrace.quality import prepare,segment_prepared,analysis_raster,to_working,Cancelled
from geotrace.automatic import preserve_work
from geotrace.editing import draw_polygon
from test_core import sample


def test_joint_palette_deterministic_hole_rare_color_and_topology():
    r=sample();rgb=np.repeat(np.repeat(r.rgb,4,axis=0),4,axis=1)
    valid=np.repeat(np.repeat(r.valid,4,axis=0),4,axis=1)
    rgb[100:112,300:312]=[245,145,190] # 0.15%, but a real coherent region.
    r=replace(r,rgb=rgb,valid=valid,transform=r.transform*Affine.scale(.25),source_size=(400,240))
    p=prepare(r);q=prepare(r)
    assert p.palette==q.palette
    assert any(np.linalg.norm(np.array(c['rgb'])-[245,145,190])<30 for c in p.palette),p.palette
    result,info=segment_prepared(p)
    assert (result.labels[valid]>0).all() and not result.labels[~valid].any()
    polygons=[f['geometry'] for f in result.features];union=unary_union(polygons)
    assert all(g.is_valid for g in polygons)
    assert sum(g.area for g in polygons)==pytest.approx(union.area)
    assert union.area==pytest.approx(valid.sum()*abs(r.transform.determinant))
    assert len(set(f['properties']['object_id'] for f in result.features))==len(result.features)


def test_excluded_groups_keep_manual_geometry_and_no_hidden_reassignment():
    r=sample();p=prepare(r);assert len(p.palette)==2
    group=int(p.assignment[40,80]);groups=[i for i in range(1,3)];groups[group-1]=0
    generated,_=segment_prepared(p,groups)
    assert not generated.labels[40,80]
    old=draw_polygon(r,None,[(70,35),(85,35),(85,48),(70,48)])
    old.features[0]['properties']['note']='Ручной объект внутри исключённого цвета'
    merged=preserve_work(generated,old,partial=True)
    assert merged.features[0]['properties']==old.features[0]['properties']
    assert merged.features[0]['geometry'].equals_exact(old.features[0]['geometry'],0)
    with pytest.raises(ValueError,match='хотя бы одну'):
        segment_prepared(p,[0,0])
    (raster_result,_)=segment_prepared(p,[1,1])
    assert set(np.unique(raster_result.labels))=={0,1}


def test_equal_color_line_separates_regions_and_cancel(tmp_path):
    r=sample();rgb=np.full((100,120,3),[215,225,170],np.uint8)
    rgb[:,59:62]=[40,45,35]
    r=replace(r,rgb=rgb,valid=np.ones((100,120),bool),source_size=(120,100))
    p=prepare(r)
    result,_=segment_prepared(p)
    objects=rasterize([(f['geometry'],i+1) for i,f in enumerate(result.features)],out_shape=r.valid.shape,transform=r.transform)
    assert objects[50,30]!=objects[50,90], 'A persistent contact must survive equal fills'
    (tmp_path/'quality-cancel').touch()
    with pytest.raises(Cancelled): segment_prepared(p,folder=tmp_path)


def test_native_source_fallback_and_project_footprint(tmp_path):
    r=sample();path=tmp_path/'native.tif'
    rgb=np.repeat(np.repeat(r.rgb,30,axis=0),30,axis=1)
    valid=np.repeat(np.repeat(r.valid,30,axis=0),30,axis=1)
    with rasterio.open(path,'w',driver='GTiff',width=3000,height=1800,count=4,dtype='uint8',crs='EPSG:4326',transform=r.transform*Affine.scale(1/30)) as dst:
        dst.write(np.moveaxis(rgb,-1,0),indexes=[1,2,3]);dst.write(valid.astype('uint8')*255,4)
        dst.colorinterp=(rasterio.enums.ColorInterp.red,rasterio.enums.ColorInterp.green,rasterio.enums.ColorInterp.blue,rasterio.enums.ColorInterp.alpha)
    working=read_raster(path)
    native,note=analysis_raster(working)
    assert native.rgb.shape[:2]==(1800,3000) and note=='Исходный TIFF'
    # A native partition projected back must preserve the exact working footprint.
    from geotrace.core import Result,vectorize
    labels=np.where(np.indices(native.valid.shape)[1]<1500,1,2).astype('uint8');labels[~native.valid]=0
    generated=Result(native,labels,vectorize(labels,native),[[230,180,80],[110,180,210]])
    converted=to_working(generated,working)
    union=unary_union([f['geometry'] for f in converted.features])
    assert union.area==pytest.approx(working.valid.sum()*abs(working.transform.determinant))
    path.unlink()
    fallback,note=analysis_raster(working)
    assert fallback is working and 'недоступен' in note


def test_native_refinement_keeps_narrow_neck_island_hole_and_junction():
    from types import SimpleNamespace
    import cv2
    from scipy import ndimage
    from geotrace.quality import refine_contacts

    objects = np.ones((44, 54), np.int32)
    objects[5:18, 5:18] = 2
    objects[5:18, 30:43] = 2
    objects[11, 18:30] = 2       # neck one overview pixel wide
    objects[8:11, 8:11] = 1     # hole within the left lobe
    objects[27, 8] = 3          # one-pixel island
    objects[25:, 30:] = 4
    objects[25:34, 37:] = 5     # junction of three regions
    shape = (83, 104)
    valid = np.ones(shape, bool)
    valid[:2, :2] = False
    evidence = np.random.default_rng(2026).uniform(size=shape).astype(np.float32)
    prepared = SimpleNamespace(raster=SimpleNamespace(valid=valid), scale=shape[1]/54,
                               boundary=evidence)
    refined = refine_contacts(objects, prepared)
    resized = cv2.resize(objects, shape[::-1], interpolation=cv2.INTER_NEAREST_EXACT)
    resized[~valid] = 0
    assert set(np.unique(resized)) == set(np.unique(refined))
    assert np.array_equal(refined > 0, valid)
    for value in range(1, 6):
        for image, expected in [(refined == value, resized == value),
                                (refined != value, resized != value)]:
            assert ndimage.label(image)[1] == ndimage.label(expected)[1]


def test_only_ambiguous_compact_colour_fragments_are_absorbed():
    from types import SimpleNamespace
    from geotrace.quality import consolidate_fragments

    objects = np.ones((60, 80), np.int32)
    objects[10:15,10:15] = 2     # low-confidence printing speck
    objects[10:15,25:30] = 3     # confident rare colour
    objects[25:27,10:35] = 4     # narrow lens
    objects[35:40,45:50] = 5     # distinct contact
    units = np.array([0,1,2,3,2,2], np.uint16)
    evidence = np.zeros(objects.shape, np.float32)
    evidence[objects == 5] = 1
    margin = np.ones_like(evidence)
    margin[objects == 3] = 12
    p = SimpleNamespace(sampled=False,boundary=evidence, line_evidence=evidence, margin=margin,
                        raster=SimpleNamespace(valid=np.ones_like(objects, bool)),
                        lab=np.zeros((*objects.shape,3),np.float32),
                        palette=[dict(id=1,share=90),dict(id=2,share=9),dict(id=3,share=1)])
    regions, classes = consolidate_fragments(objects, units, p, [1,2,3])
    actual = classes[regions]
    assert [actual[y,x] for y,x in [(12,12),(12,26),(26,20),(37,47)]] == [1,3,2,2]
    filtered = units.copy()
    filtered[units == 2] = 0
    regions, classes = consolidate_fragments(objects, filtered, p, [1,0,3])
    assert (classes[regions][objects == 2] == 0).all()


def test_local_tones_require_coherent_patches_and_preserve_other_fills():
    from geotrace.quality import refine_fill_shades
    lab = np.zeros((36,108,3),np.float32)
    labels = np.ones((36,108),np.uint8)
    shades = np.full(labels.shape,7,np.uint8)
    for i,color in enumerate((30,45,65)):
        lab[:,24*i:24*i+24,0] = color
    labels[:,72:] = 2
    labels[0,:] = 0
    actual = refine_fill_shades(lab,labels,shades)
    assert len(set(actual[18,[10,34,58]]))==3
    np.testing.assert_array_equal(actual[labels!=1],shades[labels!=1])
    # The same histogram scattered across single pixels should retain the existing partition.
    noise = np.random.default_rng(42).permutation(lab[:, :72].reshape(-1,3)).reshape(36,72,3)
    actual = refine_fill_shades(noise,labels[:,:72],shades[:,:72])
    np.testing.assert_array_equal(actual,shades[:,:72])


@pytest.mark.parametrize('stage', ['hatching', 'contacts'])
def test_final_fragment_check_runs_after_texture_and_contact_edits(stage, monkeypatch):
    from geotrace import quality
    rgb = np.full((80, 110, 3), [225, 235, 190], np.uint8)
    valid = np.ones(rgb.shape[:2], bool)
    valid[50:55, 75:80] = False
    r = replace(sample(), rgb=rgb, valid=valid, source_size=valid.shape[::-1])
    objects = np.where(valid, 1, 0).astype(np.int32)
    evidence = np.zeros(valid.shape, np.float32)
    evidence[35:40, 45:50] = 1  # genuine strongly marked enclave
    margin = np.ones_like(evidence)
    margin[12:17, 70:75] = 12  # independently confident rare fill
    p = quality.Prepared(r, np.zeros((*valid.shape, 3), np.float32), evidence,
                         np.zeros((3, 3), np.float32), objects.astype(np.uint8), margin,
                         [dict(id=1, share=80, rgb=[225, 235, 190]),
                          dict(id=2, share=19, rgb=[220, 230, 185]),
                          dict(id=3, share=1, rgb=[240, 100, 175])],
                         hatching=np.zeros(valid.shape, np.int32), line_evidence=evidence,
                         print_texture=np.zeros((27, 37), np.uint8))
    monkeypatch.setattr(quality, 'region_partition', lambda *args: (objects.copy(), np.array([0, 1], np.uint16)))

    def altered(base, units):
        modified = base.copy()
        modified[12:17, 12:17] = 2  # late ambiguous printing chip
        modified[25:27, 12:37] = 3  # narrow lens must survive
        modified[12:17, 70:75] = 4  # rare confident colour must survive
        modified[35:40, 45:50] = 5  # continuous marked boundary must survive
        modified[60:65, :5] = 6    # clipped outline must survive
        return modified, np.array([0, 1, 2, 2, 3, 1, 2], np.uint16)

    def fitted(base, units, *args, **kwargs):
        return altered(base, units) if stage == 'hatching' else (base, units)

    def reconciled(base, units, prepared, **kwargs):
        result, classes = altered(base, units) if stage == 'contacts' else (base, units)
        return result, classes, []

    monkeypatch.setattr(quality, 'fit_hatching', fitted)
    monkeypatch.setattr(quality, 'reconcile_contacts', reconciled)
    monkeypatch.setattr(quality, 'split_interior_contacts', lambda base, units, *args, **kwargs: (base, units))
    actual, _ = quality.segment_prepared(p)
    assert [actual.labels[y, x] for y, x in ((14, 14), (26, 20), (14, 72), (37, 47), (62, 2))] == [1, 2, 3, 1, 2]
    owners = rasterize([(f['geometry'], i+1) for i, f in enumerate(actual.features)],
                       out_shape=valid.shape, transform=r.transform)
    assert owners[37, 47] != owners[37, 42], 'Keep the enclave even with matching fill.'
    assert np.all(actual.labels[valid] > 0) and not actual.labels[~valid].any()
    np.testing.assert_array_equal(objects, np.where(valid, 1, 0))


def test_native_paper_colours_correct_a_coarse_staircase_without_changing_topology(monkeypatch):
    from types import SimpleNamespace
    import cv2
    from scipy import ndimage
    from geotrace import quality
    y, x = np.indices((60, 100))
    truth = np.where(x < 50, 1, 2).astype(np.int32)
    coarse = truth.copy()
    coarse[(x == 49) & ((y//3) % 2 == 0)] = 2
    rgb = np.where((x < 50)[:, :, None], [230, 205, 170], [170, 220, 235]).astype(np.uint8)
    valid = np.ones(truth.shape, bool)
    valid[10:14, 20:24] = False
    truth[~valid] = coarse[~valid] = 0
    rgb[~valid] = 0
    before = coarse.copy(), rgb.copy(), valid.copy()
    p = SimpleNamespace(raster=SimpleNamespace(valid=valid, rgb=rgb), scale=1.,
                        boundary=np.zeros(valid.shape, np.float32))
    corrected = quality.refine_contacts(coarse, p)
    monkeypatch.setattr(quality, '_contact_colour_model', lambda *args: None)
    previous = quality.refine_contacts(coarse, p)
    assert np.count_nonzero(corrected != truth) < np.count_nonzero(previous != truth)
    assert np.array_equal(corrected > 0, valid)
    for code in (1, 2):
        for expected, actual in ((coarse == code, corrected == code),
                                 (coarse != code, corrected != code)):
            assert ndimage.label(expected)[1] == ndimage.label(actual)[1]
    np.testing.assert_array_equal(coarse, before[0])
    np.testing.assert_array_equal(rgb, before[1])
    np.testing.assert_array_equal(valid, before[2])


def test_native_colour_vote_ignores_ink_and_does_not_invent_a_model_for_a_sliver():
    from types import SimpleNamespace
    from geotrace.quality import _contact_colour_model, refine_contacts
    y, x = np.indices((60, 100))
    truth = np.where(x < 50, 1, 2).astype(np.int32)
    coarse = truth.copy()
    coarse[(x == 49) & ((y//3) % 2 == 0)] = 2
    rgb = np.where((x < 50)[:, :, None], [230, 205, 170], [170, 220, 235]).astype(np.uint8)
    ink = (y >= 18) & (y < 27) & (x >= 46) & (x < 54)
    rgb[ink] = 20
    coarse[40:45, 15:17] = 3
    p = SimpleNamespace(raster=SimpleNamespace(rgb=rgb, valid=np.ones(truth.shape, bool)),
                        scale=1., boundary=np.zeros(truth.shape, np.float32))
    colour, readable, models, supported = _contact_colour_model(coarse, p)
    assert colour.dtype == np.uint8
    assert supported[1] and supported[2] and not supported[3]
    result = refine_contacts(coarse, p)
    np.testing.assert_array_equal(result[ink], coarse[ink])
    np.testing.assert_array_equal(result[40:45, 15:17], coarse[40:45, 15:17])
    assert _contact_colour_model(coarse, SimpleNamespace(raster=SimpleNamespace(valid=p.raster.valid))) is None


def _many_fill_raster():
    colours = np.array([[250, 80, 80], [250, 140, 70], [250, 200, 60], [240, 250, 100],
                        [170, 250, 90], [80, 240, 110], [70, 250, 190], [80, 240, 250],
                        [80, 170, 250], [70, 100, 250], [150, 100, 250], [220, 100, 250],
                        [250, 100, 200], [180, 50, 80], [70, 140, 80], [120, 100, 65],
                        [60, 60, 60], [200, 200, 200], [225, 180, 140], [120, 150, 180]], np.uint8)
    rgb = np.zeros((160, 200, 3), np.uint8)
    for i, colour in enumerate(colours):
        rgb[40*(i//5):40*(i//5+1), 40*(i % 5):40*(i % 5+1)] = colour
    return replace(sample(), rgb=rgb, valid=np.ones(rgb.shape[:2], bool), source_size=(200, 160))


def test_rich_palette_keeps_more_than_twelve_distinct_fills_and_is_deterministic():
    r = _many_fill_raster()
    p, repeated = prepare(r), prepare(r)
    assert 18 <= len(p.palette) <= 24
    assert p.palette == repeated.palette
    np.testing.assert_array_equal(p.assignment, repeated.assignment)
    result, _ = segment_prepared(p)
    classes = [result.labels[20+40*(i//5), 20+40*(i % 5)] for i in range(20)]
    assert len(set(classes)) >= 18
    owners = rasterize([(f['geometry'], i+1) for i, f in enumerate(result.features)],
                       out_shape=r.valid.shape, transform=r.transform)
    assert len({owners[20+40*(i//5), 20+40*(i % 5)] for i in range(20)}) == 20
    assert np.all(result.labels > 0)


def test_additional_palette_search_checks_cancellation(tmp_path, monkeypatch):
    from geotrace import quality
    checkpoint = quality.checkpoint
    count = 0

    def cancel_in_extension(folder, stage, progress):
        nonlocal count
        if 'дополнительные' in stage:
            count += 1
            if count == 2:
                (tmp_path/'quality-cancel').touch()
        return checkpoint(folder, stage, progress)

    monkeypatch.setattr(quality, 'checkpoint', cancel_in_extension)
    r = _many_fill_raster()
    before = r.rgb.copy()
    with pytest.raises(Cancelled):
        prepare(r, folder=tmp_path)
    assert count == 2
    np.testing.assert_array_equal(r.rgb, before)


def test_progress_retries_temporary_windows_file_lock(tmp_path, monkeypatch):
    from pathlib import Path
    from geotrace import quality
    replace_file = Path.replace
    calls = []
    def temporarily_locked(path, target):
        calls.append(True)
        if len(calls) < 3:
            raise PermissionError('Progress frame is being read')
        return replace_file(path, target)
    monkeypatch.setattr(Path, 'replace', temporarily_locked)
    monkeypatch.setattr(quality.time, 'sleep', lambda _: None)
    quality.checkpoint(tmp_path, 'Контроль', 60)
    assert len(calls) == 3
    assert quality.json.loads((tmp_path/'quality-progress.json').read_text())['progress'] == 60


def test_busy_progress_file_cannot_abort_the_calculation(tmp_path, monkeypatch):
    from pathlib import Path
    from geotrace import quality
    quality.checkpoint(tmp_path, 'Прежний кадр', 20)
    calls = []
    def locked(path, target):
        calls.append(True)
        raise PermissionError('Antivirus or UI holds the old progress frame')
    monkeypatch.setattr(Path, 'replace', locked)
    monkeypatch.setattr(quality.time, 'sleep', lambda _: None)
    quality.checkpoint(tmp_path, 'Новый кадр', 50)
    assert len(calls) == 5
    assert quality.json.loads((tmp_path/'quality-progress.json').read_text())['progress'] == 20
    (tmp_path/'quality-cancel').touch()
    with pytest.raises(Cancelled):
        quality.checkpoint(tmp_path, 'После отмены', 60)


def test_cancellation_is_checked_between_progress_write_retries(tmp_path, monkeypatch):
    from pathlib import Path
    from geotrace import quality
    def cancel_while_locked(path, target):
        (tmp_path/'quality-cancel').touch()
        raise PermissionError('Progress frame is being read')
    monkeypatch.setattr(Path, 'replace', cancel_while_locked)
    monkeypatch.setattr(quality.time, 'sleep', lambda _: None)
    with pytest.raises(Cancelled):
        quality.checkpoint(tmp_path, 'Остановить', 50)
