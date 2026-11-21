from types import SimpleNamespace

import numpy as np
import pytest

from geotrace.interior_contacts import split_interior_contacts


@pytest.mark.parametrize('case', ['contact', 'brightness', 'grid', 'short', 'nodata'])
def test_tonal_split_requires_a_long_dark_contact_and_chromatic_difference(case):
    objects = np.ones((90, 160), np.int32)
    if case == 'nodata':
        objects[38:48, 20:27] = 0
    lab = np.full((*objects.shape, 3), [94., 138., 130.], np.float32)
    lab[:, 80:] = [97., 134., 132.]
    if case == 'brightness':
        lab[:, 80:] = [99., 138., 130.]
    rgb = np.full((*objects.shape, 3), [247, 233, 230], np.uint8)
    boundary = np.zeros(objects.shape, np.float32)
    evidence = np.zeros_like(boundary)
    boundary[:, 79:81] = .55
    evidence[:, 79:81] = .4
    rgb[:, 79:81] = 90
    if case == 'grid':
        rgb[:, 79:81] = 255
    if case == 'short':
        boundary[:] = 0
        evidence[:] = 0
        boundary[30:60, 79:81] = .55
        evidence[30:60, 79:81] = .4
        rgb[:30, 79:81] = rgb[60:, 79:81] = [247, 233, 230]
    prepared = SimpleNamespace(lab=lab, boundary=boundary, line_evidence=evidence,
                               raster=SimpleNamespace(rgb=rgb))
    classes = np.array([0, 2], np.uint16)
    result, units = split_interior_contacts(objects, classes, prepared)
    assert (result[20, 30] != result[20, 130]) == (case in ('contact', 'nodata'))
    np.testing.assert_array_equal(result == 0, objects == 0)
    np.testing.assert_array_equal(units[result], classes[objects])


def multiple_tones(count=4, interruption='contact', rotate=False):
    objects = np.ones((100, count*90), np.int32)
    objects[8:18, 12:23] = 0  # Internal NoData hole must survive every split.
    lab = np.empty((*objects.shape, 3), np.float32)
    rgb = np.full((*objects.shape, 3), [247, 233, 230], np.uint8)
    boundary = np.zeros(objects.shape, np.float32)
    evidence = boundary.copy()
    for i in range(count):
        lab[:, i*90:(i+1)*90] = [94+i, 138-4*i, 130+2*i]
        if i:
            rgb[:, i*90-1:i*90+1] = 90
            boundary[:, i*90-1:i*90+1] = .55
            evidence[:, i*90-1:i*90+1] = .4
    if interruption != 'contact':
        x = (count-1)*90
        if interruption == 'brightness':
            lab[:, x:] = [99, 138-4*(count-2), 130+2*(count-2)]
        if interruption == 'grid':
            rgb[:, x-1:x+1] = 255
        if interruption == 'short':
            rgb[:30, x-1:x+1] = rgb[60:, x-1:x+1] = [247, 233, 230]
            boundary[:30, x-1:x+1] = boundary[60:, x-1:x+1] = 0
            evidence[:30, x-1:x+1] = evidence[60:, x-1:x+1] = 0
    if rotate:
        objects, lab, rgb, boundary, evidence = [np.rot90(a).copy() for a in
                                                 (objects, lab, rgb, boundary, evidence)]
    prepared = SimpleNamespace(lab=lab, boundary=boundary, line_evidence=evidence,
                               raster=SimpleNamespace(rgb=rgb))
    return objects, prepared


@pytest.mark.parametrize('count', [3, 4])
@pytest.mark.parametrize('rotate', [False, True])
def test_all_supported_contacts_are_found_inside_one_display_group(count, rotate):
    objects, prepared = multiple_tones(count, rotate=rotate)
    classes = np.array([0, 2], np.uint16)
    before = objects.copy()
    result, units = split_interior_contacts(objects, classes, prepared)
    centers = result[50, 45::90] if not rotate else result[::-1, :][45::90, 50]
    assert len(np.unique(centers)) == count
    assert len(np.unique(result[result > 0])) == count
    np.testing.assert_array_equal(result == 0, objects == 0)
    np.testing.assert_array_equal(units[result], classes[objects])
    np.testing.assert_array_equal(objects, before)


@pytest.mark.parametrize('interruption', ['brightness', 'grid', 'short'])
@pytest.mark.parametrize('rotate', [False, True])
def test_additional_split_requires_its_own_colour_and_ink_support(interruption, rotate):
    objects, prepared = multiple_tones(4, interruption, rotate)
    classes = np.array([0, 2], np.uint16)
    result, units = split_interior_contacts(objects, classes, prepared)
    centers = result[50, 45::90] if not rotate else result[::-1, :][45::90, 50]
    assert centers[0] != centers[1]
    assert centers[1] != centers[2]
    assert centers[2] == centers[3]
    np.testing.assert_array_equal(result == 0, objects == 0)
    np.testing.assert_array_equal(units[result], classes[objects])


def test_repeated_contact_checks_can_be_cancelled_without_mutating_the_input():
    from geotrace.quality import Cancelled
    objects, prepared = multiple_tones(4)
    before = objects.copy()
    calls = []
    def cancel():
        calls.append(True)
        if len(calls) == 2:
            raise Cancelled('Остановлено')
    with pytest.raises(Cancelled):
        split_interior_contacts(objects, np.array([0, 2], np.uint16), prepared, check=cancel)
    np.testing.assert_array_equal(objects, before)
