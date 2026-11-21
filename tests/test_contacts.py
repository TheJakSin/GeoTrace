from types import SimpleNamespace

import numpy as np

from geotrace.contacts import reconcile_contacts
from geotrace.hatching import STRIDE


def pair():
    regions=np.ones((60,80),np.int32)
    regions[:,40:]=2
    zeros=np.zeros(regions.shape,np.float32)
    p=SimpleNamespace(lab=np.zeros((*zeros.shape,3),np.float32),boundary=zeros.copy(),
                      line_evidence=zeros.copy(),hatching=zeros.copy(),
                      print_texture=np.zeros_like(regions[::STRIDE,::STRIDE],np.uint8))
    p.raster = SimpleNamespace(rgb=np.full((*regions.shape, 3), 180, np.uint8))
    return regions,np.array([0,1,1]),p


def test_weak_contact_merges_but_respects_colour_class_and_line():
    regions,classes,p=pair()
    regions[:4]=0
    merged,units,review=reconcile_contacts(regions,classes,p)
    assert np.array_equal(merged==0,regions==0)
    assert np.array_equal(units[merged],classes[regions])
    assert len(np.unique(merged[regions>0]))==1 and not review
    p.line_evidence[:,39:41]=1
    assert np.array_equal(reconcile_contacts(regions,classes,p)[0],regions)
    p.line_evidence[:]=0
    classes[2]=2
    assert np.array_equal(reconcile_contacts(regions,classes,p)[0],regions)


def test_texture_disagreement_keeps_contact_and_marks_location():
    regions,classes,p=pair()
    p.print_texture[:,14:]=3
    kept,_,review=reconcile_contacts(regions,classes,p)
    assert np.array_equal(kept,regions)
    assert len(review)==1 and review[0][0]==39
    p.hatching[:,40:]=2
    assert not reconcile_contacts(regions,classes,p)[2]


def test_local_contact_evidence_respects_ink_thin_regions_and_print():
    regions, classes, p = pair()
    p.lab[:, 40:, 1] = 4
    joined, _, _ = reconcile_contacts(regions, classes, p)
    assert joined[30, 20] == joined[30, 60]

    p.raster.rgb[:, 39:41] = 20
    assert np.array_equal(reconcile_contacts(regions, classes, p)[0], regions)
    p.raster.rgb[:] = 180
    p.print_texture[:, 14:] = 3
    assert np.array_equal(reconcile_contacts(regions, classes, p)[0], regions)

    p.print_texture[:] = 0
    narrow = regions.copy()
    narrow[:, :37] = 0
    assert np.array_equal(reconcile_contacts(narrow, classes, p)[0], narrow)
    narrow[:, :37] = 3
    assert np.array_equal(reconcile_contacts(narrow, np.array([0, 1, 1, 2]), p)[0], narrow)

    # Two neighbouring pieces have the same means but different local flanks.
    p.lab[:, 40:50, 1] = 12
    p.lab[:, 50:, 1] = 1
    assert np.array_equal(reconcile_contacts(regions, classes, p)[0], regions)


def test_paper_under_dark_print_can_reunite_automatic_classes():
    regions, _, p = pair()
    classes = np.array([0, 1, 2])
    p.raster.rgb[:] = [244, 239, 226]
    p.raster.rgb[8:50:4, 9:32] = 54
    merged, units, _ = reconcile_contacts(regions, classes, p)
    assert merged[30, 15] == merged[30, 65]
    assert units[merged[30, 15]] in (1, 2)

    # A continuous contact in the original scan separates the two fills.
    p.raster.rgb[:, 39:41] = 40
    assert np.array_equal(reconcile_contacts(regions, classes, p)[0], regions)
    p.raster.rgb[:, 39:41] = [244, 239, 226]
    p.raster.rgb[:, 40:] = [224, 246, 224]
    assert np.array_equal(reconcile_contacts(regions, classes, p)[0], regions)


def test_pale_fragments_cannot_bridge_a_vetoed_edge_or_grow_through_satellites():
    from geotrace.contacts import join_printed_fragments

    ids = np.arange(5, dtype=np.int32)
    size = np.array([0, 1000, 400, 200, 80])
    left = np.array([1, 2, 3])
    right = np.array([2, 3, 4])
    merged = join_printed_fragments(ids, size, left, right, np.ones(3, bool))
    assert merged.tolist() == [0, 1, 1, 1, 4]

    vetoed = join_printed_fragments(ids, size, np.array([1, 2, 1]),
                                    np.array([2, 3, 3]), np.array([True, True, False]))
    assert vetoed.tolist() == [0, 1, 1, 3, 4]


def test_dark_paper_agreement_is_opt_in_and_keeps_nodata_and_inputs():
    regions, _, p = pair()
    regions[6:12, 6:12] = 0
    classes = np.array([0, 1, 2])
    p.raster.rgb[20:45:4, 12:26] = 40
    before = regions.copy(), p.raster.rgb.copy(), classes.copy()
    assert np.array_equal(reconcile_contacts(regions, classes, p)[0], regions)
    merged, units, _ = reconcile_contacts(regions, classes, p, match_paper=True)
    assert merged[30, 20] == merged[30, 60]
    np.testing.assert_array_equal(merged > 0, regions > 0)
    assert units[merged[30, 20]] in (1, 2)
    np.testing.assert_array_equal(regions, before[0])
    np.testing.assert_array_equal(p.raster.rgb, before[1])
    np.testing.assert_array_equal(classes, before[2])


def test_paper_match_requires_independent_ink_colour_texture_and_probe_agreement():
    from copy import deepcopy
    regions, _, original = pair()
    classes = np.array([0, 1, 2])
    for veto in ('ink', 'line', 'paper', 'local_colour', 'hatching', 'texture', 'narrow'):
        p = deepcopy(original)
        local = regions.copy()
        if veto == 'ink':
            p.raster.rgb[:, 39:41] = 20
        elif veto == 'line':
            p.line_evidence[:, 39:41] = 1
        elif veto == 'paper':
            p.raster.rgb[:, 40:] = [190, 170, 180]
        elif veto == 'local_colour':
            # Equal regional paper models must not hide a narrow changed rim.
            p.lab[:, 40:48, 1] = 12
        elif veto == 'hatching':
            p.hatching[:, 40:] = 1
        elif veto == 'texture':
            p.print_texture[:, 14:] = 3
        else:
            local[:, :37] = 0
        for rotate in (False, True):
            if rotate:
                local = np.rot90(local).copy()
                for field in ('lab', 'boundary', 'line_evidence', 'hatching', 'print_texture'):
                    setattr(p, field, np.rot90(getattr(p, field)).copy())
                p.raster.rgb = np.rot90(p.raster.rgb).copy()
            kept, _, _ = reconcile_contacts(local, classes, p, match_paper=True)
            np.testing.assert_array_equal(kept, local, err_msg=veto)
