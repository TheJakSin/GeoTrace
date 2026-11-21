from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from geotrace.hatching import hatch_patches, fit_hatching


@pytest.mark.parametrize('angle', [0., .6, np.pi/2])
def test_repetitive_fill_with_crossing_contact_and_short_mark(angle):
    y, x = np.indices((130, 170))
    field = (x > 18) & (x < 150) & (y > 22) & (y < 105)
    rgb = np.full((*field.shape, 3), 220, np.uint8)
    phase = (x[field]*np.cos(angle) + y[field]*np.sin(angle))*2*np.pi/5
    rgb[field] = (211 + 26*np.cos(phase))[:, None]
    rgb[30:90, 48] = 20
    rgb[22:106, 86:88] = 20
    valid = np.ones(field.shape, bool)
    patches = hatch_patches(rgb, valid)
    overlap = (patches > 0) & field
    assert overlap.sum() > .8*field.sum()
    assert overlap.sum() > .8*(patches > 0).sum()

    objects = np.where(field, y//12+2, 1).astype(np.int32)
    classes = np.ones(objects.max()+1, np.uint16)
    classes[0] = 0
    prepared = SimpleNamespace(raster=SimpleNamespace(rgb=rgb, valid=valid),
                               palette=[{}], boundary=np.zeros(field.shape,np.float32))
    result, units = fit_hatching(objects, classes, patches, prepared)
    assert result[45,45] == result[47,52] == result[75,60]
    assert result[55,60] != result[55,120]
    assert (units[result] == 1).all()
    classes[objects[55,60]] = 0
    partial, units = fit_hatching(objects, classes, patches, prepared)
    np.testing.assert_array_equal(units[partial], classes[objects])


def test_plain_background_with_label_and_single_line():
    rgb = np.full((110,160,3),220,np.uint8)
    rgb[:,78:81] = 34
    cv2.putText(rgb,'Q IV',(15,65),cv2.FONT_HERSHEY_SIMPLEX,1,(25,25,25),2)
    assert not hatch_patches(rgb,np.ones(rgb.shape[:2],bool)).any()


@pytest.mark.parametrize('obstacle', ['ambiguous', 'distinct', 'ink', 'nodata', 'excluded'])
def test_narrow_neck_through_a_second_colour(obstacle):
    y, x = np.indices((80, 130))
    patch = (y >= 20) & (y <= 59) & (x >= 12) & (x <= 117)
    # Two wide ends meet through a narrow, differently classified interruption.
    gap = (x >= 62) & (x <= 68)
    patch &= ~gap | ((y >= 38) & (y <= 42))
    valid = np.ones_like(patch)
    if obstacle == 'nodata':
        valid[gap] = False
    classes = np.array([0, 1, 1, 2, 2], np.uint16)
    obj = np.where(patch & ~gap, np.where(x < 62, 1, 2), 3)
    obj[gap & patch] = 4
    obj[~valid] = 0
    if obstacle == 'excluded':
        classes[4] = 0
    rgb = np.full((*patch.shape,3),200,np.uint8)
    if obstacle == 'ink':
        rgb[:,65:67] = 0
    centers = np.array([[100,120,120],[100,140,120]],np.float32)
    color = np.where((obj==1)|(obj==2),1,2)
    lab = centers[color-1]
    if obstacle != 'distinct':
        lab[gap & patch,1] = 131
    prepared = SimpleNamespace(raster=SimpleNamespace(rgb=rgb,valid=valid),palette=[{},{}],
                               boundary=np.zeros(y.shape,np.float32),lab=lab,
                               centers=centers,assignment=color)
    result, units = fit_hatching(obj, classes, patch.astype(np.int32), prepared,
                                 automatic_palette=obstacle != 'excluded')
    assert (result[40,30] == result[40,100]) == (obstacle == 'ambiguous')
    if obstacle == 'excluded':
        assert (units[result][gap & patch] == 0).all()
    assert (units[result][~valid] == 0).all()


def test_tapered_hatched_arm_follows_existing_outline_and_skips_parallel_neighbour():
    y, x = np.indices((90,160))
    body = (y>=31) & (y<=55) & (x>=95) & (x<=146)
    arm = (x>=13) & (x<95) & (y>=40) & (y<=44)
    objects = np.ones(y.shape,np.int32)
    objects[body] = np.where(y[body]<44,2,3)
    objects[arm] = 4
    objects[48:53,13:80] = 5
    classes = np.array([0,2,1,1,1,1],np.uint16)
    patches = (body | (arm & (x>62))).astype(np.int32)
    patches[arm & (x<61)] = 2
    patches[48:53,13:80] = 3
    valid = np.ones(y.shape,bool)
    rgb = np.full((*y.shape,3),210,np.uint8)
    prepared = SimpleNamespace(raster=SimpleNamespace(rgb=rgb,valid=valid),
                               palette=[{},{}],boundary=np.zeros(y.shape,np.float32))
    result, units = fit_hatching(objects,classes,patches,prepared)
    assert result[42,20] == result[39,110]
    assert result[49,20] != result[42,20]
    np.testing.assert_array_equal(units[result],classes[objects])


@pytest.mark.parametrize('kind', ['chip','strong_contact','uncut_island','other_class','thin_lens','nodata'])
def test_cleanup_is_limited_to_same_class_cut_remnants(kind):
    from geotrace.hatching import absorb_cut_remnants

    original = np.ones((40,50), np.int32)
    original[5:35,10:40] = 2
    fitted = original.copy()
    fitted[fitted == 2] = 4
    fragment = np.zeros(fitted.shape, bool)
    fragment[17:23,10] = True
    if kind == 'thin_lens':
        fragment[:] = False
        fragment[10:24,10] = True
    owner = 3 if kind == 'uncut_island' else 2
    original[fragment] = owner
    fitted[fragment] = owner
    classes = np.array([0,1,1,1,1], np.uint16)
    boundary = np.zeros(fitted.shape, np.float32)
    if kind == 'strong_contact':
        boundary[:] = .95
    if kind == 'other_class':
        classes[owner] = 2
    if kind == 'nodata':
        original[fragment] = fitted[fragment] = 0

    output = absorb_cut_remnants(original,fitted,classes,4,boundary)
    if kind == 'chip':
        assert np.all(output[fragment] == 1)
    else:
        np.testing.assert_array_equal(output,fitted)
    np.testing.assert_array_equal(classes[output],classes[fitted])


def test_colour_hatch_requires_matching_light_rhythm_and_respects_an_existing_field():
    from geotrace.hatching import add_colour_hatching, spectral_grid

    y, x = np.indices((90, 140))
    field = (x > 20) & (x < 122) & (y > 22) & (y < 63)
    rgb = np.full((*field.shape, 3), 185, np.uint8)
    wave = np.cos(2*np.pi*x[field]/5)
    rgb[field] = 185 + wave[:, None] * [50, -8, -9]
    valid = np.ones(field.shape, bool)
    valid[:, :25] = False
    # A weak but correctly oriented brightness peak supplies independent support.
    score, angle, frequency = spectral_grid(rgb, valid)
    spectrum = np.minimum(score, .12), angle, frequency
    empty = np.zeros(field.shape, np.int32)
    found = add_colour_hatching(rgb, valid, spectrum, empty)
    assert np.count_nonzero(found[field & valid]) > .65 * (field & valid).sum()
    assert not found[~valid].any()
    wrong = spectrum[0], angle + np.pi/2, frequency
    assert not add_colour_hatching(rgb, valid, wrong, empty).any()
    old = empty.copy()
    old[32:46, 65:79] = 7
    np.testing.assert_array_equal(add_colour_hatching(rgb, valid, spectrum, old), old)


@pytest.mark.parametrize('obstacle', ['none', 'strong_line', 'inner_line', 'other_colour', 'other_field', 'fork'])
def test_tracker_follows_taper_without_absorbing_a_parallel_neighbour(obstacle):
    from geotrace.hatching import extend_fitted_tips

    fitted = np.ones((62, 130), np.int32)
    fitted[24:36, 56:110] = 6
    fitted[25:35, 44:56] = 3
    fitted[27:33, 32:44] = 4
    fitted[36:41, 69:96] = 5
    if obstacle == 'other_field':
        fitted[fitted == 3] = 7
    if obstacle == 'fork':
        fitted[30:35, 44:56] = 2
    patch = fitted != 1
    barrier = np.zeros_like(patch)
    if obstacle in ('strong_line', 'inner_line'):
        barrier[:, 55 if obstacle == 'strong_line' else 56] = True
    assignment = np.ones_like(fitted)
    colors = np.asarray([[80, 120, 120], [80, 140, 120]], dtype=np.float32)
    units = [0, 2, 1, 1, 1, 1, 1, 1]
    if obstacle == 'other_colour':
        units[3] = 2
        assignment[fitted == 3] = 2
    original = fitted.copy()
    extend_fitted_tips(fitted, 6, units, 6, patch, colors[assignment-1], colors,
                       assignment, barrier)
    if obstacle == 'none':
        assert np.all(fitted[(original == 3) | (original == 4)] == 6)
        np.testing.assert_array_equal(fitted[original == 5], original[original == 5])
    else:
        np.testing.assert_array_equal(fitted, original)
