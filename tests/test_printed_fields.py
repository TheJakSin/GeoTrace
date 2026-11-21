import numpy as np
import pytest

from geotrace.contacts import paper_colours
from geotrace.printed_fields import join_open_fields, restore_tinted_strips


@pytest.mark.parametrize('rotate', [False, True])
@pytest.mark.parametrize('large_anchor', [False, True])
@pytest.mark.parametrize('case', ['print', 'flat', 'contact', 'strong_evidence', 'subtle_colour',
                                  'brightness', 'other_texture', 'few_samples', 'nodata',
                                  'coloured_contact', 'coloured_print'])
def test_two_fragments_need_matching_paper_print_and_a_clear_source_opening(case, large_anchor, rotate):
    # A printed patch is surrounded by a much larger map unit. The former
    # area limit and three-fragment rule excluded this ordinary two-part case.
    objects = np.ones((90, 160), np.int32)
    objects[30:60, 60:90] = 2
    raw = np.full((*objects.shape, 3), [246, 126, 136], np.uint8)
    if case == 'subtle_colour':
        raw[objects == 2] = [246, 130, 136]
    if case == 'brightness':
        raw[objects == 2] = [236, 126, 136]
    if case != 'flat':
        raw[33:57:3, 63:87, 0] = 45
    boundary = np.zeros(objects.shape, np.float32)
    if case in ('contact', 'strong_evidence', 'nodata', 'coloured_contact'):
        rim = np.zeros(objects.shape, bool)
        rim[29:61, 59:91] = True
        rim[31:59, 61:89] = False
        if case == 'contact':
            raw[rim, 0] = 45
        elif case == 'strong_evidence':
            boundary[rim] = 1
        elif case == 'nodata':
            objects[rim] = 0
        else:
            raw[rim] = [215, 150, 150]
            # A short faded portion must not turn a long coloured contact
            # into a valid route for joining its two sides.
            raw[40:46, 59:62] = [246, 126, 136]
    if case == 'coloured_print':
        raw[35:45, 59:62] = [215, 150, 150]
    if not large_anchor:
        objects, raw, boundary = [a[15:75, 40:110].copy() for a in (objects, raw, boundary)]
    if rotate:
        objects, raw, boundary = [np.rot90(a).copy() for a in (objects, raw, boundary)]
    before_objects, before_raw = objects.copy(), raw.copy()
    area = np.bincount(objects.ravel(), minlength=3)
    paper, _, depth = paper_colours(objects, raw, area)
    texture = np.zeros((3, 64))
    texture[1:, 0] = 20
    if case == 'other_texture':
        texture[2, [0, 3]] = [0, 20]
    if case == 'few_samples':
        texture[2, 0] = 4
    positions, vertical = [], []
    for dy, dx in ((1, 0), (0, 1)):
        a = objects[:objects.shape[0]-dy, :objects.shape[1]-dx]
        b = objects[dy:, dx:]
        y, x = np.nonzero((a != b) & (a > 0) & (b > 0))
        positions.extend(y*objects.shape[1]+x)
        vertical.extend([bool(dy)]*len(y))
    owner = join_open_fields(objects, area, paper, depth, texture, raw, boundary,
                             np.asarray(positions, dtype=int), np.asarray(vertical, dtype=bool))
    assert (owner[1] == owner[2]) == (case in ('print', 'coloured_print'))
    np.testing.assert_array_equal(owner[objects] == 0, objects == 0)
    np.testing.assert_array_equal(objects, before_objects)
    np.testing.assert_array_equal(raw, before_raw)


@pytest.mark.parametrize('rotate', [False, True])
def test_printed_patch_cannot_bypass_a_direct_contact_between_large_regions(rotate):
    objects = np.ones((90, 200), np.int32)
    objects[:, 100:] = 2
    objects[30:60, 85:115] = 3
    raw = np.full((*objects.shape, 3), [246, 126, 136], np.uint8)
    raw[:, 99:101, 0] = 40
    raw[33:57:3, 88:112, 0] = 45
    if rotate:
        objects, raw = [np.rot90(a).copy() for a in (objects, raw)]
    area = np.bincount(objects.ravel())
    paper, _, depth = paper_colours(objects, raw, area)
    texture = np.zeros((4, 64))
    texture[1:, 0] = 20
    positions, vertical = [], []
    for dy, dx in ((1, 0), (0, 1)):
        a = objects[:objects.shape[0]-dy, :objects.shape[1]-dx]
        b = objects[dy:, dx:]
        y, x = np.nonzero((a != b) & (a > 0) & (b > 0))
        positions.extend(y*objects.shape[1]+x)
        vertical.extend([bool(dy)]*len(y))
    owner = join_open_fields(objects, area, paper, depth, texture, raw, np.zeros(objects.shape),
                             np.asarray(positions, dtype=int), np.asarray(vertical, dtype=bool))
    assert area[1] > 6000 and area[2] > 6000
    assert owner[1] != owner[2]


@pytest.mark.parametrize('rotate', [False, True])
def test_faded_coloured_contact_is_kept_when_a_third_fragment_joins_the_group(rotate):
    objects = np.repeat(np.array([[1, 2, 3]], np.int32), 60, axis=1).repeat(60, axis=0)
    raw = np.full((*objects.shape, 3), [246, 126, 136], np.uint8)
    raw[8:52:3, 8:55, 0] = 45
    raw[:, 119:121] = [215, 150, 150]
    raw[20:26, 119:121] = [246, 126, 136]
    if rotate:
        objects, raw = [np.rot90(a).copy() for a in (objects, raw)]
    area = np.bincount(objects.ravel())
    paper, _, depth = paper_colours(objects, raw, area)
    texture = np.zeros((4, 64))
    texture[1:, 0] = 20
    positions, vertical = [], []
    for dy, dx in ((1, 0), (0, 1)):
        a = objects[:objects.shape[0]-dy, :objects.shape[1]-dx]
        b = objects[dy:, dx:]
        y, x = np.nonzero((a != b) & (a > 0) & (b > 0))
        positions.extend(y*objects.shape[1]+x)
        vertical.extend([bool(dy)]*len(y))
    owner = join_open_fields(objects, area, paper, depth, texture, raw, np.zeros(objects.shape),
                             np.asarray(positions, dtype=int), np.asarray(vertical, dtype=bool))
    assert owner[1] == owner[2]
    assert owner[1] != owner[3]


@pytest.mark.parametrize('crossing', ['open', 'line_tip', 'compact_mark', 'short_separator', 'barrier', 'nodata', 'different_fill'])
def test_rare_strip_uses_independent_colour_samples_and_respects_crossings(crossing):
    objects = np.ones((160, 300), np.int32)
    objects[78:83, 28:81] = 2
    objects[78:83, 126:182] = 3
    units = np.array([0, 1, 2, 2], np.int32)
    raw = np.full((*objects.shape, 3), [240, 130, 145], np.uint8)
    raw[78:83, 28:182] = [216, 116, 130]
    if crossing == 'barrier':
        raw[59:101, 106:108, 0] = 78
    if crossing == 'compact_mark':
        raw[76:85, 104:111, 0] = 78
    if crossing == 'short_separator':
        raw[70:91, 106:108, 0] = 78
    if crossing == 'line_tip':
        raw[59:80, 106:108, 0] = 78
    if crossing == 'nodata':
        objects[:, 106:108] = 0
    if crossing == 'different_fill':
        raw[78:83, 90:119] = [225, 145, 152]
    area = np.bincount(objects.ravel())
    paper, _, _ = paper_colours(objects, raw, area)
    result, colors = restore_tinted_strips(objects, units, area, paper, raw)
    assert (result[80, 55] == result[80, 155]) == (crossing in ('open', 'line_tip', 'compact_mark'))
    np.testing.assert_array_equal(result == 0, objects == 0)
    if crossing in ('open', 'line_tip', 'compact_mark'):
        assert colors[result[80, 104]] == 2
        assert colors[result[74, 104]] == 1


@pytest.mark.parametrize('interruption', ['none', 'line', 'other_print', 'flat'])
def test_three_pale_fragments_require_continuous_paper_and_compatible_print(interruption):
    objects = np.repeat(np.array([[1, 2, 3]], np.int32), 20, axis=1).repeat(18, axis=0)
    raw = np.full((*objects.shape, 3), [246, 126, 136], np.uint8)
    if interruption != 'flat':
        raw[::4, 3:12, 0] = 40
    if interruption == 'line':
        raw[:, 39:41, 0] = 30
    area = np.bincount(objects.ravel())
    paper, _, depth = paper_colours(objects, raw, area)
    texture = np.zeros((4, 64))
    texture[1:, 0] = 20
    if interruption == 'other_print':
        texture[3, [0, 3]] = [0, 20]
    y, x = np.nonzero(objects[:, :-1] != objects[:, 1:])
    positions = y*objects.shape[1]+x
    joined = join_open_fields(objects, area, paper, depth, texture, raw, np.zeros(objects.shape),
                              positions, np.zeros(len(y), bool))
    assert (len(set(joined[1:])) == 1) == (interruption == 'none')


@pytest.mark.parametrize('interruption', ['print', 'symbol', 'line', 'nodata', 'different_fill', 'broad'])
def test_strip_banks_can_confirm_a_print_change_without_crossing_separators(interruption):
    objects = np.ones((92, 240), np.int32)
    low, high = (30, 42) if interruption != 'broad' else (20, 60)
    objects[high:] = 2
    objects[low:high] = np.repeat(np.array([[3, 4, 5]], np.int32), 80, axis=1)
    raw = np.full((*objects.shape, 3), [205, 109, 158], np.uint8)
    raw[low:high] = [251, 126, 136]
    if interruption == 'symbol':
        raw[low+2:high-2, 159:161, 0] = 40
    if interruption == 'line':
        raw[low-8:high+8, 159:161, 0] = 40
    if interruption == 'nodata':
        objects[low:high, 159:161] = 0
    if interruption == 'different_fill':
        raw[low:high, 160:, 1:] = [143, 148]
    area = np.bincount(objects.ravel(), minlength=6)
    paper, _, depth = paper_colours(objects, raw, area)
    depth[3:] = 25
    texture = np.zeros((6, 64))
    texture[3, 1] = texture[4, 9] = texture[5, 17] = 30
    boundary = np.zeros(objects.shape)
    boundary[[low-1, low, high-1, high]] = 1
    positions, vertical = [], []
    for dy, dx in [(1, 0), (0, 1)]:
        a = objects[:objects.shape[0]-dy, :objects.shape[1]-dx]
        b = objects[dy:, dx:]
        y, x = np.nonzero((a != b) & (a > 0) & (b > 0))
        positions.extend(y*objects.shape[1]+x)
        vertical.extend([bool(dy)]*len(y))
    joined = join_open_fields(objects, area, paper, depth, texture, raw, boundary,
                              np.array(positions), np.array(vertical))
    assert (joined[3] == joined[5]) == (interruption in ('print', 'symbol'))

@pytest.mark.parametrize('vertical', [False, True])
@pytest.mark.parametrize('mark, expected', [('short', True), ('separator', False), ('different_flanks', False)])
def test_partial_print_needs_matching_exposed_flanks_and_no_transverse_separator(vertical, mark, expected):
    from geotrace.printed_fields import _printed_gap

    objects = np.ones((64, 84), np.int32)
    objects[:, 42:] = 2
    objects[:24] = 0
    objects[39:] = 0
    raw = np.full((*objects.shape, 3), [248, 126, 136], np.uint8)
    raw[27:35, 41:43, 0] = 60
    if mark == 'separator':
        raw[14:49, 41:43, 0] = 60
    if mark == 'different_flanks':
        raw[:, 43:, 1:] = [146, 153]
    y = np.arange(25, 38)
    x = np.full(len(y), 41)
    if vertical:
        objects = objects.T.copy()
        raw = raw.transpose(1, 0, 2).copy()
        args = x, y, x+1, y
    else:
        args = y, x, y, x+1
    assert _printed_gap(objects, raw, 1, 2, *args) == expected


@pytest.mark.parametrize('transpose', [False, True])
def test_bright_rim_cleanup_keeps_bridges_holes_and_visible_pigment(transpose):
    from scipy import ndimage
    from geotrace.printed_fields import _trim_bright_rim

    mask = np.zeros((28, 46), bool)
    mask[7:21, 2:15] = True
    mask[7:21, 31:44] = True
    mask[13, 15:31] = True
    mask[11:14, 6:9] = False
    raw = np.full((*mask.shape, 3), [250, 126, 140], np.uint8)
    raw[9:19, 4:13] = [216, 117, 133]
    raw[9:19, 33:42] = [216, 117, 133]
    pigment = mask & (raw[:, :, 0] == 216)
    if transpose:
        mask, pigment = mask.T, pigment.T
        raw = raw.transpose(1, 0, 2)
    result = _trim_bright_rim(mask, raw, np.array([220., 117., 133.]))
    assert result.sum() < mask.sum()
    assert not np.any(result & ~mask)
    assert result[pigment].all()
    assert ndimage.label(result)[1] == ndimage.label(mask)[1]
    assert ndimage.label(~np.pad(result, 1))[1] == ndimage.label(~np.pad(mask, 1))[1]
    bridge = result[15:31, 13] if transpose else result[13, 15:31]
    assert bridge.all()
