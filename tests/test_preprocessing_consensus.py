from dataclasses import replace

import cv2
import numpy as np
import pytest

from geotrace.preprocessing import consensus_colors
from geotrace import quality
from test_core import sample


@pytest.mark.parametrize('rotate', [False, True])
def test_opposing_paper_samples_restore_the_fill_under_a_compact_ink_mark(rotate):
    filled = np.full((35, 39, 3), [190, 220, 140], np.uint8)
    ink = np.zeros(filled.shape[:2], bool)
    ink[14:21, 16:23] = True
    filled[ink] = 40
    valid = np.ones(ink.shape, bool)
    reconstructed = np.full_like(filled, [190, 220, 140])
    reconstructed[ink] = [213, 226, 169]
    if rotate:
        filled, ink, valid, reconstructed = [np.rot90(a).copy() for a in (filled, ink, valid, reconstructed)]
    before = filled.copy(), reconstructed.copy()
    output, count = consensus_colors(filled, ink, valid, reconstructed)
    assert 0 < count <= ink.sum()
    np.testing.assert_array_equal(output[ink], np.tile([190, 220, 140], (ink.sum(), 1)))
    np.testing.assert_array_equal(filled, before[0])
    np.testing.assert_array_equal(reconstructed, before[1])


@pytest.mark.parametrize('case', ['competing', 'disagree', 'nodata', 'disconnected', 'corner', 'no_ink'])
@pytest.mark.parametrize('rotate', [False, True])
def test_unsupported_or_competing_colours_keep_the_existing_reconstruction(case, rotate):
    filled = np.full((19, 19, 3), [190, 220, 140], np.uint8)
    ink = np.zeros((19, 19), bool)
    ink[9, 9] = True
    valid = np.ones(ink.shape, bool)
    if case == 'competing':
        for y, x in [(8, 8), (10, 10), (8, 10), (10, 8)]:
            filled[y, x] = [240, 175, 190]
    if case == 'disagree':
        for y, x in [(10, 9), (9, 10), (10, 10), (10, 8)]:
            filled[y, x] = [240, 175, 190]
    if case == 'nodata':
        valid[:] = False
        valid[9, :] = True  # One supported axis alone is insufficient.
    if case == 'disconnected':
        valid[8:11, 8:11] = False
        valid[9, 9] = True
    if case == 'corner':
        y, x = np.indices(valid.shape)
        valid[:] = abs(y-9) == abs(x-9)
    if case == 'no_ink':
        ink[:] = False
    filled[ink] = 40
    reconstructed = np.full_like(filled, [204, 219, 158])
    if rotate:
        filled, ink, valid, reconstructed = [np.rot90(a).copy() for a in (filled, ink, valid, reconstructed)]
    output, count = consensus_colors(filled, ink, valid, reconstructed)
    assert count == 0
    np.testing.assert_array_equal(output, cv2.medianBlur(reconstructed, 5))


def test_large_ink_mask_is_processed_in_cancellable_chunks_without_mutating_input():
    filled = np.full((400, 400, 3), [190, 220, 140], np.uint8)
    ink = np.indices(filled.shape[:2]).sum(axis=0) % 2 == 0
    filled[ink] = 40
    valid = np.ones(ink.shape, bool)
    reconstructed = np.full_like(filled, [190, 220, 140])
    reconstructed[ink] = [213, 226, 169]
    before = reconstructed.copy()
    calls = []
    def cancel():
        calls.append(True)
        if len(calls) == 2:
            raise quality.Cancelled('Остановлено')
    with pytest.raises(quality.Cancelled):
        consensus_colors(filled, ink, valid, reconstructed, check=cancel)
    np.testing.assert_array_equal(reconstructed, before)
    output, count = consensus_colors(filled, ink, valid, reconstructed)
    assert count > 65536  # Both chunks contributed independent valid pixels.
    assert output.shape == filled.shape
    np.testing.assert_array_equal(output[8:-8, 8:-8],
                                  np.full_like(output[8:-8, 8:-8], [190, 220, 140]))


def test_diagonal_probes_can_follow_a_connected_staircase_beside_nodata():
    filled = np.full((19, 19, 3), [190, 220, 140], np.uint8)
    valid = np.ones((19, 19), bool)
    valid[9, 10] = False
    ink = np.zeros_like(valid)
    ink[9, 9] = True
    filled[ink] = 40
    reconstructed = np.full_like(filled, [190, 220, 140])
    reconstructed[ink] = [213, 226, 169]
    before = valid.copy()
    output, count = consensus_colors(filled, ink, valid, reconstructed)
    assert count == 1
    np.testing.assert_array_equal(valid, before)
    np.testing.assert_array_equal(output[9, 9], [190, 220, 140])


def test_palette_shades_and_boundary_evidence_do_not_depend_on_recovered_fill(monkeypatch):
    raster = sample()
    rgb = np.full((120, 180, 3), [190, 220, 140], np.uint8)
    rgb[:, 95:] = [245, 227, 215]
    cv2.putText(rgb, 'Cu', (70, 72), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (30, 30, 30), 5)
    raster = replace(raster, rgb=rgb, valid=np.ones(rgb.shape[:2], bool), source_size=(180, 120))
    original_rgb = rgb.copy()
    refined = quality.prepare(raster)
    monkeypatch.setattr(quality, 'consensus_colors', lambda filled, ink, valid, reconstructed, **options:
                        (cv2.medianBlur(reconstructed, 5), 0))
    baseline = quality.prepare(raster)
    assert refined.boundary_lab is not None
    np.testing.assert_array_equal(refined.centers, baseline.centers)
    np.testing.assert_array_equal(refined.shades, baseline.shades)
    np.testing.assert_array_equal(refined.boundary, baseline.boundary)
    np.testing.assert_array_equal(refined.boundary_lab, baseline.lab)
    np.testing.assert_array_equal(refined.raster.rgb, original_rgb)
    assert sum(c['pixels'] for c in refined.palette) == raster.valid.sum()
    assert not np.array_equal(refined.lab, baseline.lab)
    native = replace(raster, rgb=cv2.resize(rgb, (360, 240), interpolation=cv2.INTER_NEAREST),
                     valid=np.ones((240, 360), bool), source_size=(360, 240))
    refined_native = quality.prepare(native, appearance=refined)
    baseline_native = quality.prepare(native, appearance=baseline)
    np.testing.assert_array_equal(refined_native.lab, baseline_native.lab)
    np.testing.assert_array_equal(refined_native.boundary, baseline_native.boundary)
