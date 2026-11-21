"""Reconsider neighbouring regions using independent colour, print and line cues."""
import cv2
import numpy as np
from scipy import ndimage
from skimage.measure import label

from .bank_tracking import track_banks
from .corridors import contact_graph, join_corridors
from .hatching import STRIDE, absorb_cut_remnants
from .printed_fields import bright_side, join_open_fields, restore_tinted_strips


def print_codes(spectrum):
    score, angle, frequency = spectrum
    orientation = np.rint(angle*8/np.pi).astype(int)%8
    band = np.clip(np.rint(frequency/2),0,6).astype(int)
    return np.where(score>=.09,1+orientation+8*band,0).astype(np.uint8)


def sample_contact_flanks(regions, lab, light, y, x, axis):
    """Sample on both sides only while the probe stays inside the adjoining regions."""
    dy, dx = (1, 0) if axis == 0 else (0, 1)
    height, width = regions.shape
    left, right = regions[y, x], regions[y + dy, x + dx]
    inside = np.ones(len(y), dtype=bool)
    left_light = np.zeros(len(y), np.float32)
    right_light = np.zeros_like(left_light)
    for distance in range(1, 5):
        ay, ax = y - distance * dy, x - distance * dx
        by, bx = y + (distance + 1) * dy, x + (distance + 1) * dx
        inside &= (ay >= 0) & (ax >= 0) & (by < height) & (bx < width)
        ay, ax = np.maximum(ay, 0), np.maximum(ax, 0)
        by, bx = np.minimum(by, height - 1), np.minimum(bx, width - 1)
        inside &= (regions[ay, ax] == left) & (regions[by, bx] == right)
        if distance >= 3:
            left_light += .5 * light[ay, ax]
            right_light += .5 * light[by, bx]
    chroma_distance = np.linalg.norm(lab[ay, ax, 1:] - lab[by, bx, 1:], axis=1)
    center_light = np.minimum(light[y, x], light[y + dy, x + dx])
    dark = np.minimum(left_light, right_light) - center_light > 22
    return inside, inside & (chroma_distance < 6), inside & dark


def paper_colours(regions, raw, size):
    """Estimate the underlying paper from the brightest tenth of each region."""
    light = raw[:, :, 0]
    # Sorting pixel codes bounds memory by image size even on fragmented sheets.
    ordered = np.sort((regions * 256 + light).ravel())
    ranks = np.maximum(0, np.cumsum(size) - size//10 - 1)
    cutoff = (ordered[ranks] % 256).astype(np.uint8)
    exposed = light >= cutoff[regions]
    labels = regions[exposed]
    samples = np.bincount(labels, minlength=len(size))
    paper = np.stack([np.bincount(labels, weights=band[exposed], minlength=len(size))
                      / np.maximum(1, samples) for band in raw.transpose(2, 0, 1)], axis=1)
    mean = np.bincount(regions.ravel(), weights=light.ravel(), minlength=len(size)) / np.maximum(1, size)
    return paper, samples, paper[:, 0] - mean


def join_printed_fragments(lookup, size, left, right, supported):
    """Every shared boundary must pass. A satellite must touch the original pair."""
    if not supported.any():
        return lookup
    count = len(lookup)
    left, right = lookup[left], lookup[right]
    keys = np.minimum(left, right).astype(np.int64)*count + np.maximum(left, right)
    pairs, inverse = np.unique(keys, return_inverse=True)
    veto = np.bincount(inverse, weights=~supported) > 0
    a, b = np.divmod(pairs, count)
    area = np.bincount(lookup, weights=size, minlength=count)
    smaller = np.minimum(area[a], area[b])
    choices = np.flatnonzero(~veto & (a != b) & (smaller >= 40))
    forbidden = {}
    for i, j in zip(a[veto], b[veto]):
        if i != j:
            forbidden.setdefault(i, set()).add(j)
            forbidden.setdefault(j, set()).add(i)
    owner = np.arange(count, dtype=lookup.dtype)
    cores = {}
    for k in choices[np.argsort(-smaller[choices], kind='stable')]:
        i, j = owner[a[k]], owner[b[k]]
        if i == j or (i in cores and j in cores):
            continue
        if j in cores or (i not in cores and area[j] > area[i]):
            i, j = j, i
        if i in cores and (area[j] > area[i] or
                           (a[k] not in cores[i] and b[k] not in cores[i])):
            continue
        if j in forbidden.get(i, ()):
            continue
        owner[j] = i
        cores.setdefault(i, (i, j))
        forbidden.setdefault(i, set()).update(forbidden.get(j, ()))
    return owner[lookup]


def reconcile_contacts(objects, classes, prepared, *, match_paper=False):
    # A region identifier can recur on disconnected islands after hatching.
    regions = label(objects,background=0,connectivity=1).astype(np.int32)
    count = int(regions.max())+1
    if count<3:
        return objects, classes, []
    ids, first = np.unique(regions,return_index=True)
    units = np.zeros(count,classes.dtype)
    units[ids] = classes[objects.ravel()[first]]
    size = np.bincount(regions.ravel(),minlength=count)
    count_safe = np.maximum(size,1)
    ink = ndimage.maximum_filter(prepared.line_evidence,size=3)
    codes, strong, strokes, positions = [], [], [], []
    raw = cv2.cvtColor(prepared.raster.rgb, cv2.COLOR_RGB2LAB)
    light = raw[:, :, 0]
    probed, matched = [], []
    flank_samples = []
    for axis in (0,1):
        head = (slice(None,-1),slice(None)) if axis==0 else (slice(None),slice(None,-1))
        tail = (slice(1,None),slice(None)) if axis==0 else (slice(None),slice(1,None))
        a,b = regions[head],regions[tail]
        cut = (a!=b)&(a>0)&(b>0)
        y,x=np.nonzero(cut)
        dy, dx = (1, 0) if axis == 0 else (0, 1)
        first, n1 = bright_side(raw, regions, y, x, -dy, -dx)
        second, n2 = bright_side(raw, regions, y+dy, x+dx, dy, dx)
        delta = first - second
        enough = (n1 >= 5) & (n2 >= 5)
        probed.append(enough)
        matched.append(enough & (np.minimum(first[:, 0], second[:, 0]) >= 215)
                       & (abs(delta[:, 0]) <= 15) & (np.linalg.norm(delta[:, 1:], axis=1) <= 6))
        flank_samples.append(sample_contact_flanks(regions, prepared.lab, light, y, x, axis))
        positions.append(y*regions.shape[1]+x)
        smaller,larger=np.minimum(a[cut],b[cut]),np.maximum(a[cut],b[cut])
        codes.append(smaller.astype(np.int64)*count+larger)
        strong.append(np.maximum(prepared.boundary[head][cut],prepared.boundary[tail][cut])>.65)
        strokes.append(np.maximum(ink[head][cut],ink[tail][cut])>.5)
    pairs,inv,length=np.unique(np.concatenate(codes),return_inverse=True,return_counts=True)
    left,right=np.divmod(pairs,count)
    perimeter=np.bincount(left,weights=length,minlength=count)+np.bincount(right,weights=length,minlength=count)
    shared=length/np.minimum(perimeter[left],perimeter[right])
    strength=np.bincount(inv,weights=np.concatenate(strong))/length
    continuity=np.bincount(inv,weights=np.concatenate(strokes))/length
    chroma=np.stack([np.bincount(regions.ravel(),weights=channel.ravel(),minlength=count)/count_safe
                     for channel in prepared.lab[:,:,1:].transpose(2,0,1)],axis=1)
    color=np.linalg.norm(chroma[left]-chroma[right],axis=1)

    cells=regions[::STRIDE,::STRIDE]
    histogram=np.bincount((cells.astype(np.int64)*64+prepared.print_texture).ravel(),
                          minlength=count*64).reshape(count,64)
    samples=histogram.sum(axis=1)
    histogram=histogram/np.maximum(1,samples[:,None])
    # Direction is periodic. Similar hatch angles on either side of a bin
    # boundary should contribute to agreement within the same frequency band.
    oriented=histogram[:,1:57].reshape(count,7,8)
    histogram[:,1:57]=(.5*oriented+.25*(np.roll(oriented,1,axis=2)
                                         +np.roll(oriented,-1,axis=2))).reshape(count,56)
    # Only compare similarly printed, unhatched fills. Small regions without
    # enough texture samples can still appear in the review queue.
    hatch_fraction=np.bincount(regions.ravel(),weights=(prepared.hatching>0).ravel(),minlength=count)/count_safe
    weak_contact = ((units[left] == units[right]) & (units[left] > 0) & (length >= 12)
                    & (strength < .45) & (continuity < .35)
                    & (hatch_fraction[left] < .2) & (hatch_fraction[right] < .2))
    candidates = weak_contact & (shared >= .2) & (color <= 2)
    # Weaker geometry or mean colour agreement needs independent local evidence.
    # A continuous dark trough, a third region or a different print can veto it.
    available, agreeing, dark = (np.bincount(inv, weights=np.concatenate(values))
                                 for values in zip(*flank_samples))
    similar_flanks = ((available >= .5 * length) & (agreeing >= .60 * available)
                      & (dark <= .25 * available))
    local_candidates = weak_contact & (shared >= .12) & (color <= 5) & similar_flanks
    lookup=np.arange(count,dtype=np.int32)
    used=np.zeros(count,bool)
    uncertain=[]
    eligible = candidates | local_candidates
    for k in np.flatnonzero(eligible)[np.argsort(-shared[eligible],kind='stable')]:
        a,b=left[k],right[k]
        if used[a] or used[b]:
            continue
        difference=.5*np.abs(histogram[a]-histogram[b]).sum()
        threshold = .5 if candidates[k] else .30
        if min(samples[a],samples[b])>=6 and difference<=threshold:
            lookup[b]=a
            used[[a,b]]=True
        elif candidates[k]:
            uncertain.append(k)
    # In light fills, letters and symbols can shift the inferred class. Check
    # exposed paper locally and across each region before removing the division.
    paper, bright_count, shading = paper_colours(regions, raw, size)
    delta = paper[left] - paper[right]
    probes = np.bincount(inv, weights=np.concatenate(probed))
    matches = np.bincount(inv, weights=np.concatenate(matched))
    local_paper = (probes >= .65*length) & (matches >= .55*probes)
    brightness = abs(delta[:, 0])
    printed = ((units[left] > 0) & (units[right] > 0) & (length >= 12)
               & (np.minimum(bright_count[left], bright_count[right]) >= 12)
               & (np.minimum(paper[left, 0], paper[right, 0]) > 223)
               & (np.maximum(shading[left], shading[right]) > 12)
               & ((brightness < 8) | ((brightness < 14) & local_paper))
               & (np.linalg.norm(delta[:, 1:], axis=1) < 4)
               & (strength < .35) & (continuity < .25)
               & (available >= .3*length) & (dark <= .5*available)
               & ((agreeing >= .6*available) | local_paper)
               & (hatch_fraction[left] < .2) & (hatch_fraction[right] < .2)
               & (np.minimum(samples[left], samples[right]) >= 6))
    for k in np.flatnonzero(printed):
        printed[k] = .5*np.abs(histogram[left[k]] - histogram[right[k]]).sum() < .4
    if match_paper:
        # Automatic palette slots can disagree because of print density even
        # in darker fills. Require independent exposed-paper, local-flank and
        # texture agreement; a real inked contact vetoes the union.
        same_paint = ((units[left] > 0) & (units[right] > 0) & (length >= 16)
                      & (strength < .25) & (continuity < .20) & (shared >= .15)
                      & (np.minimum(bright_count[left], bright_count[right]) >= 12)
                      & (brightness <= 4) & (np.linalg.norm(delta[:, 1:], axis=1) <= 2.5)
                      & (available >= .65*length) & (agreeing >= .85*available)
                      & (dark <= .10*available) & (hatch_fraction[left] < .1)
                      & (hatch_fraction[right] < .1)
                      & (np.minimum(samples[left], samples[right]) >= 6))
        for k in np.flatnonzero(same_paint):
            same_paint[k] = .5*np.abs(histogram[left[k]]-histogram[right[k]]).sum() <= .2
        printed |= same_paint
    lookup = join_printed_fragments(lookup, size, left, right, printed)
    # Reuse face positions and line support from the contact measurements.
    positions = np.concatenate(positions)
    y, x = np.divmod(positions, regions.shape[1])
    vertical = np.arange(len(inv)) < len(codes[0])
    midpoints = np.column_stack((np.bincount(inv, weights=y+.5*vertical)/length,
                                 np.bincount(inv, weights=x+.5*~vertical)/length))
    graph = contact_graph(lookup, left, right, length, strength, midpoints)
    lookup = join_corridors(lookup, units, regions, size, paper, hatch_fraction, light, graph)
    lookup = track_banks(lookup, units, regions, size, paper, hatch_fraction, raw, prepared.boundary,
                         positions, vertical, left, right, inv)
    grouped = lookup[regions]
    areas = np.bincount(grouped.ravel(), minlength=count)
    grouped_paper, _, depth = paper_colours(grouped, raw, areas)
    texture_counts = np.zeros_like(histogram)
    np.add.at(texture_counts, lookup, samples[:, None] * histogram)
    owner = join_open_fields(grouped, areas, grouped_paper, depth, texture_counts,
                             raw, prepared.boundary, positions, vertical)
    lookup = owner[lookup]
    grouped = owner[grouped]
    areas = np.bincount(owner, weights=areas, minlength=count)
    tinted, tinted_units = restore_tinted_strips(grouped, units, areas, grouped_paper, raw)
    if tinted is not grouped:
        tinted = absorb_cut_remnants(grouped, tinted, tinted_units, len(units), prepared.boundary)
    uncertain = [k for k in uncertain if lookup[left[k]] != lookup[right[k]]]
    # Choose an actual contact pixel near its middle, away from end junctions.
    points = []
    if uncertain:
        order = np.argsort(inv,kind='stable')
        offsets = np.r_[0,np.cumsum(length)]
        for k in uncertain:
            samples = positions[order[offsets[k]:offsets[k+1]]]
            y,x = np.divmod(samples,regions.shape[1])
            mid = np.argmin((y-y.mean())**2+(x-x.mean())**2)
            points.append((int(x[mid]),int(y[mid])))
    if tinted is grouped and np.array_equal(lookup, np.arange(count)):
        return objects,classes,points
    return tinted,tinted_units,points
