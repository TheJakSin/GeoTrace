from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from geotrace.contacts import reconcile_contacts
from geotrace.hatching import STRIDE


@pytest.mark.parametrize('interrupt', ['symbol', 'line', 'nodata', 'different_fill', 'thin_line'])
def test_narrow_passage_connects_only_across_a_local_print_mark(interrupt):
    regions = np.ones((76, 250), np.int32)
    regions[40:] = 2
    regions[33:40] = 3
    regions[33:40, 116:125] = 4
    regions[33:40, 125:130] = 5
    regions[33:40, 130:] = 6
    colors = np.array([[0, 0, 0], [141, 180, 115], [156, 178, 123],
                       [249, 246, 228], [241, 239, 219], [44, 44, 44], [249, 246, 228]], np.uint8)
    if interrupt == 'nodata':
        regions[regions == 5] = 0
    if interrupt == 'different_fill':
        colors[6] = [243, 225, 227]
    rgb = colors[regions]
    if interrupt == 'line':
        rgb[17:57, 126:129] = 44
    if interrupt == 'thin_line':
        rgb[regions == 5] = colors[3]
        regions[33:40, 125:130] = 4
        regions[33:40, 129] = 5
        rgb[regions == 5] = 44
    blank = np.zeros(regions.shape, np.float32)
    prepared = SimpleNamespace(lab=cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32),
                               boundary=blank.copy(), line_evidence=blank.copy(), hatching=blank.copy(),
                               raster=SimpleNamespace(rgb=rgb),
                               print_texture=np.zeros_like(regions[::STRIDE, ::STRIDE], np.uint8))
    classes = np.array([0, 1, 2, 3, 4, 5, 3])
    result, units, _ = reconcile_contacts(regions, classes, prepared)
    assert (result[36, 15] == result[36, 213]) == (interrupt == 'symbol')
    assert np.all(units[result[regions == 1]] == 1)
    assert np.all(units[result[regions == 2]] == 2)
    np.testing.assert_array_equal(result == 0, regions == 0)


@pytest.mark.parametrize('interrupt', ['none', 'line', 'nodata', 'different_fill'])
def test_long_strip_keeps_both_sides_aligned_when_neighbouring_polygons_change(interrupt):
    rows, cols = np.indices((74, 360))
    strip = (rows >= 31) & (rows < 43)
    # The upper bank changes before the lower one. The two large ends thus
    # have no common pair of exterior polygons.
    regions = np.where(rows < 31, np.where(cols < 165, 1, 2), np.where(cols < 190, 3, 4))
    regions[strip] = np.digitize(cols[strip], [142, 163, 185, 207]) + 5
    colors = np.array([[0, 0, 0], [117, 175, 110], [128, 168, 98], [163, 151, 100],
                       [150, 158, 107], [250, 246, 230], [243, 240, 224], [240, 239, 220],
                       [246, 242, 226], [250, 246, 230]], np.uint8)
    if interrupt == 'different_fill':
        colors[9] = [245, 223, 218]
    if interrupt == 'nodata':
        regions[strip & (cols == 163)] = 0
    rgb = colors[regions]
    if interrupt == 'line':
        rgb[20:55, 162:164] = 32
    blank = np.zeros(regions.shape, np.float32)
    evidence = blank.copy()
    evidence[[30, 31, 42, 43]] = 1
    prepared = SimpleNamespace(lab=cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32),
                               boundary=evidence, line_evidence=blank, hatching=blank,
                               raster=SimpleNamespace(rgb=rgb),
                               print_texture=np.zeros_like(regions[::STRIDE, ::STRIDE], np.uint8))
    classes = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 5])
    result, units, _ = reconcile_contacts(regions.astype(np.int32), classes, prepared)
    assert (result[37, 80] == result[37, 280]) == (interrupt == 'none')
    np.testing.assert_array_equal(units[result[~strip]], classes[regions[~strip]])
    np.testing.assert_array_equal(result == 0, regions == 0)


def test_long_route_excludes_spurs_and_keeps_an_indirect_confirmed_boundary():
    from collections import defaultdict
    from geotrace.bank_tracking import _join_routes

    neighbours = defaultdict(set)
    contacts = {}
    for a, b, x in [(1, 2, 15), (2, 3, 36), (3, 4, 60), (3, 5, 42)]:
        neighbours[a].add(b)
        neighbours[b].add(a)
        contacts[a, b] = np.array([20., x])
    area = np.array([0, 2000, 60, 80, 1900, 50])
    lookup = np.arange(len(area))
    units = np.array([0, 1, 2, 3, 1, 2])
    paper = np.full((len(area), 3), [250., 126., 138.])
    vetoes = defaultdict(set)
    assert _join_routes(neighbours, contacts, vetoes, area, units, paper, lookup).tolist() == [0, 1, 1, 1, 1, 5]

    contacts[1, 3] = None
    vetoes[1].add(3)
    vetoes[3].add(1)
    np.testing.assert_array_equal(_join_routes(neighbours, contacts, vetoes, area, units, paper, lookup), lookup)


@pytest.mark.parametrize('pattern, expected', [('single', True), ('parallel', False), ('transverse', False)])
def test_hatch_like_strip_requires_a_single_lengthwise_mark(pattern, expected):
    from geotrace.bank_tracking import _single_ink_line

    mask = np.ones((13, 82), dtype=bool)
    light = np.full(mask.shape, 245, np.uint8)
    light[[0, -1]] = 60
    if pattern == 'parallel':
        light[[4, 8]] = 80
    elif pattern == 'transverse':
        light[:, 5::6] = 80
    else:
        light[6] = 80
    assert _single_ink_line(mask, light) == expected


@pytest.mark.parametrize('opening, expected', [([], False), ([4, 8, 12], False), ([7, 8, 9], True)])
def test_dark_contact_requires_an_unbroken_patch_between_both_fills(opening, expected):
    from geotrace.bank_tracking import _paper_opening

    labels = np.ones((18, 22), np.int32)
    labels[:, 11:] = 2
    paper = np.tile([250., 126., 138.], (3, 1))
    raw = paper[labels].astype(np.uint8)
    raw[:, 10:12, 0] = 60
    raw[opening, 10:12, 0] = 250
    y = np.arange(18)
    x = np.full_like(y, 10)
    assert _paper_opening(labels, raw, 1, 2, paper, y, x, y, x+1) == expected


@pytest.mark.parametrize('separation, barrier, join', [(80, False, True), (12, False, False), (80, True, False)])
def test_two_routes_at_the_same_polygon_require_separate_mouths(separation, barrier, join):
    from collections import defaultdict
    from geotrace.bank_tracking import _join_routes

    area = np.array([0, 2500, 80, 80, 1800, 80, 80, 1600])
    neighbours, vetoes, contacts = defaultdict(set), defaultdict(set), {}
    for row, path in [(0, [1, 2, 3, 4]), (separation, [1, 5, 6, 7])]:
        for i, (a, b) in enumerate(zip(path, path[1:])):
            neighbours[a].add(b)
            neighbours[b].add(a)
            contacts[a, b] = np.array([row, 20.*i])
    if barrier:
        vetoes[3].add(6)
        vetoes[6].add(3)
    lookup = np.arange(len(area))
    joined = _join_routes(neighbours, contacts, vetoes, area, np.ones_like(area),
                          np.tile([250., 126., 138.], (len(area), 1)), lookup)
    np.testing.assert_array_equal(joined, [0, 1, 1, 1, 1, 1, 1, 1] if join else lookup)


@pytest.mark.parametrize('interruption', ['mark', 'left_tip', 'right_tip', 'line', 'different_fill', 'nodata'])
def test_compact_print_can_occupy_two_touching_fragments_in_a_bounded_passage(interruption):
    regions = np.ones((76, 280), np.int32)
    regions[41:] = 2
    regions[33:41] = 3
    regions[33:41, 130:138] = 4
    regions[33:41, 138:144] = 5
    regions[33:41, 144:] = 6
    colors = np.array([[0, 0, 0], [141, 180, 115], [156, 178, 123],
                       [249, 246, 228], [105, 105, 105], [60, 60, 60], [249, 246, 228]], np.uint8)
    if interruption == 'different_fill':
        colors[6] = [243, 225, 227]
    if interruption == 'nodata':
        regions[33:41, 138:140] = 0
    if interruption in ('left_tip', 'right_tip'):
        regions[33:37, 144] = 1
        regions[38:41, 144] = 2
        if interruption == 'left_tip':
            regions = regions[:, ::-1].copy()
    rgb = colors[regions]
    if interruption == 'line':
        rgb[13:61, 135:139] = 40
    blank = np.zeros(regions.shape, np.float32)
    prepared = SimpleNamespace(lab=cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32),
                               boundary=blank.copy(), line_evidence=blank.copy(), hatching=blank.copy(),
                               raster=SimpleNamespace(rgb=rgb),
                               print_texture=np.zeros_like(regions[::STRIDE, ::STRIDE], np.uint8))
    classes = np.array([0, 1, 2, 3, 4, 5, 3])
    result, units, _ = reconcile_contacts(regions, classes, prepared)
    assert (result[36, 80] == result[36, 200]) == (interruption in ('mark', 'left_tip', 'right_tip'))
    np.testing.assert_array_equal(units[result[regions == 1]], 1)
    np.testing.assert_array_equal(units[result[regions == 2]], 2)
    np.testing.assert_array_equal(result == 0, regions == 0)
