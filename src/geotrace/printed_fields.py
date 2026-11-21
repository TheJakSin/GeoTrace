"""Recover connected light fills and thin, sparsely represented colour strips."""
from collections import defaultdict

import cv2
import numpy as np
from scipy import ndimage
from skimage.segmentation import watershed

from .bank_tracking import _paper_opening, _tracks_contact


def bright_side(raw, regions, y, x, dy, dx):
    """Read the lightest ink-free candidate along three short paths inside one region."""
    height, width = regions.shape
    owner = regions[y, x]
    best = np.zeros((len(y), 3), dtype=np.uint8)
    samples = np.zeros(len(y), dtype=np.uint8)
    for shift in (-2, 0, 2):
        sy, sx = y + shift * abs(dx), x + shift * abs(dy)
        inside = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
        sy, sx = np.clip(sy, 0, height-1), np.clip(sx, 0, width-1)
        inside &= (regions[sy, sx] == owner) & (regions[(y+sy)//2, (x+sx)//2] == owner)
        for distance in range(1, 7):
            iy, ix = sy + distance*dy, sx + distance*dx
            inside &= (iy >= 0) & (iy < height) & (ix >= 0) & (ix < width)
            iy, ix = np.clip(iy, 0, height-1), np.clip(ix, 0, width-1)
            inside &= regions[iy, ix] == owner
            value = raw[iy, ix]
            brighter = inside & (value[:, 0] > best[:, 0])
            best[brighter] = value[brighter]
            samples += inside
    return best.astype(np.int16), samples


def _printed_gap(objects, raw, i, j, y, x, ny, nx):
    """Require exposed matching flanks and exclude a sustained transverse stroke."""
    vertical = ny != y
    enough = np.zeros(len(y), bool)
    same = np.zeros(len(y), bool)
    for orientation in (False, True):
        mask = vertical == orientation
        fy, fx, gy, gx = y[mask], x[mask], ny[mask], nx[mask]
        if not len(fy):
            continue
        dy, dx = int(orientation), int(not orientation)
        first, n1 = bright_side(raw, objects, fy, fx, -dy, -dx)
        second, n2 = bright_side(raw, objects, gy, gx, dy, dx)
        enough[mask] = (n1 >= 5) & (n2 >= 5)
        same[mask] = ((np.minimum(first[:, 0], second[:, 0]) >= 220)
                      & (abs(first[:, 0]-second[:, 0]) < 20)
                      & (np.linalg.norm(first[:, 1:]-second[:, 1:], axis=1) < 6))
    agreement = np.count_nonzero(enough & same)
    if enough.sum() < .5*len(y) or agreement < max(12, .35*enough.sum()):
        return False

    y0, x0 = max(0, y.min()-18), max(0, x.min()-18)
    roi = np.s_[y0:ny.max()+19, x0:nx.max()+19]
    local = objects[roi]
    light = raw[roi][:, :, 0].astype(np.float32)
    coords = np.column_stack(np.nonzero((local == i) | (local == j)))
    center = np.array([y.mean()-y0, x.mean()-x0])
    coords = coords[np.linalg.norm(coords-center, axis=1) < 20]
    along = np.linalg.eigh(np.cov(coords.T))[1][:, 1]
    ink = ((light < 160) & (cv2.GaussianBlur(light, (0, 0), 3)-light > 15)).astype(np.uint8)
    barrier = np.zeros(local.shape, bool)
    for angle in np.arange(16)*np.pi/16:
        direction = np.array([np.sin(angle), np.cos(angle)])
        if abs(direction @ along) > .7:
            continue
        dx, dy = round(9*np.cos(angle)), round(9*np.sin(angle))
        line = np.zeros((19, 19), np.uint8)
        cv2.line(line, (9-dx, 9-dy), (9+dx, 9+dy), 1)
        barrier |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, line) > 0
    return not (barrier[y-y0, x-x0].any() or barrier[ny-y0, nx-x0].any())


def _follows_light_strip(objects, area, paper, raw, boundary, bounds, i, j, y, x, ny, nx):
    """Verify both banks across a print interruption in two slender light pieces."""
    if not (5 <= len(y) <= 105) or min(area[i], area[j]) < 35:
        return False
    if min(paper[i, 0], paper[j, 0]) < 231:
        return False
    light = np.minimum(raw[y, x, 0], raw[ny, nx, 0])
    support = np.maximum(boundary[y, x], boundary[ny, nx]) > .65
    if (light < 155).mean() > .80 or support.mean() > .80:
        return False
    printed = (min(paper[i, 0], paper[j, 0]) < 245 or (light < 155).mean() > .48
               or support.mean() > .65 or np.count_nonzero(light > min(paper[i, 0], paper[j, 0])-60) < 3)
    if printed and not _printed_gap(objects, raw, i, j, y, x, ny, nx):
        return False
    for ident in (i, j):
        coords = np.column_stack(np.nonzero(objects[bounds[ident-1]] == ident))
        minor, major = np.linalg.eigvalsh(np.cov(coords.T))
        if major < 3*minor or area[ident] > 15*np.linalg.norm(np.ptp(coords, axis=0)):
            return False
    xy = np.column_stack(((y+ny)*.5, (x+nx)*.5))
    axis = np.linalg.eigh(np.cov(xy.T))[1][:, 1]
    ordered = xy[np.argsort(xy @ axis)]
    samples = (xy.mean(axis=0), ordered[:len(xy)//2].mean(axis=0),
               ordered[len(xy)//2:].mean(axis=0))
    # Print repetition may vary across the cut. Geometry and visible fill
    # independently constrain the candidate strip at every sampled section.
    hatch = np.zeros(len(area))
    return any(_tracks_contact(i, j, point, 4, objects, boundary, paper, area, hatch)
               for point in samples)


def join_open_fields(objects, area, paper, depth, texture_counts, raw, boundary, positions, vertical):
    """Require exposed paper on every link and reject barriers within a proposed group."""
    n = len(area)
    bounds = ndimage.find_objects(objects)
    y, x = np.divmod(positions, objects.shape[1])
    ny, nx = y + vertical, x + ~vertical
    a, b = objects[y, x], objects[ny, nx]
    eligible = (a != b) & (np.minimum(area[a], area[b]) >= 24)
    keys = np.minimum(a, b).astype(np.int64) * n + np.maximum(a, b)
    indices = np.flatnonzero(eligible)
    indices = indices[np.argsort(keys[indices], kind='stable')]
    pairs, starts, lengths = np.unique(keys[indices], return_index=True, return_counts=True)
    sample_count = texture_counts.sum(axis=1)
    texture = texture_counts / np.maximum(1, sample_count[:, None])
    neighbours, barriers = defaultdict(set), defaultdict(set)
    strict_pairs = set()
    for key, start, length in zip(pairs, starts, lengths):
        if length < 3:
            continue
        i, j = divmod(int(key), n)
        large = max(area[i], area[j]) > 6000
        if large and min(area[i], area[j]) > 1500:
            # Keep this direct contact as a veto. A small printed fragment must
            # not become a route around a division between two large regions.
            barriers[i].add(j)
            barriers[j].add(i)
            continue
        face = indices[start:start+length]
        fy, fx, gy, gx = y[face], x[face], ny[face], nx[face]
        dark = np.minimum(raw[fy, fx, 0], raw[gy, gx, 0]) < 150
        supported = np.maximum(boundary[fy, fx], boundary[gy, gx]) > .65
        tracked = _follows_light_strip(objects, area, paper, raw, boundary, bounds, i, j, fy, fx, gy, gx)
        texture_delta = .5*abs(texture[i]-texture[j]).sum()
        # Two fragments have less mutual context than a chain. Require close
        # paper and print, weak ink support, and an actual connected opening
        # in the source pixels before treating their division as a print artefact.
        opening = False
        similar_pair = (length >= 8 and min(sample_count[i], sample_count[j]) >= 6
                        and abs(paper[i, 0]-paper[j, 0]) <= 8
                        and np.linalg.norm(paper[i, 1:]-paper[j, 1:]) <= 3
                        and texture_delta <= .3 and dark.mean() <= .5 and supported.mean() <= .5)
        if similar_pair:
            # Matching paper and print provide an independent reference for
            # coloured ink. Without that agreement, a colour change can be
            # part of the fill itself rather than a stroke on top of it.
            paper_chroma = (paper[i, 1:]+paper[j, 1:])*.5
            coloured = ((np.minimum(raw[fy, fx, 0], raw[gy, gx, 0]) >= 150)
                        & (np.maximum(np.linalg.norm(raw[fy, fx, 1:]-paper_chroma, axis=1),
                                      np.linalg.norm(raw[gy, gx, 1:]-paper_chroma, axis=1)) > 12))
            if coloured.mean() > .5:
                # A faded opening cannot override the remaining coloured
                # contact, including through another fragment in the group.
                barriers[i].add(j)
                barriers[j].add(i)
                continue
            opening = _paper_opening(objects, raw, i, j, paper, fy, fx, gy, gx)
            if opening:
                strict_pairs.add((i, j))
        if large and (i, j) not in strict_pairs:
            barriers[i].add(j)
            barriers[j].add(i)
            continue
        different_print = min(sample_count[i], sample_count[j]) >= 8 and texture_delta > .55
        if ((different_print and not tracked) or min(paper[i, 0], paper[j, 0]) < 224 or np.linalg.norm(paper[i, 1:]-paper[j, 1:]) > 5
                or supported.mean() > .82 or dark.mean() > .81):
            barriers[i].add(j)
            barriers[j].add(i)
        elif length >= 5 and (tracked or opening or (not similar_pair and
                _paper_opening(objects, raw, i, j, paper, fy, fx, gy, gx))):
            neighbours[i].add(j)
            neighbours[j].add(i)

    owner = np.arange(n, dtype=objects.dtype)
    remaining = set(neighbours)
    while remaining:
        stack = [remaining.pop()]
        group = set(stack)
        while stack:
            adjacent = neighbours[stack.pop()] & remaining
            stack.extend(adjacent)
            group.update(adjacent)
            remaining.difference_update(adjacent)
        if len(group) < 2 or max(depth[i] for i in group) <= 12 or any(barriers[i] & group for i in group):
            continue
        if len(group) == 2 and tuple(sorted(group)) not in strict_pairs:
            continue
        anchor = max(group, key=lambda i: (area[i], -i))
        if all(np.linalg.norm(paper[i, 1:]-paper[anchor, 1:]) <= 5 for i in group):
            owner[list(group)] = anchor
    return owner


def _strip_anchors(objects, units, area):
    """Find repeated slender examples of a fill occupying little of the sheet."""
    coverage = np.bincount(units, weights=area)
    rare = (coverage > 0) & (coverage <= .02 * area[1:].sum())
    rare[0] = False
    anchors = defaultdict(list)
    bounds = ndimage.find_objects(objects)
    for i in np.flatnonzero(rare[units] & (area >= 150) & (area <= 4000)):
        box = bounds[i-1]
        coords = np.column_stack(np.nonzero(objects[box] == i))
        span = np.linalg.norm(np.ptp(coords, axis=0))
        if area[i] > 11 * span:
            continue
        minor, major = np.linalg.eigvalsh(np.cov(coords.T))
        if major >= 3 * minor:
            anchors[units[i]].append(i)
    return anchors


def _link_aligned(core, permitted, strokes):
    """Close short gaps along one direction while respecting forbidden pixels."""
    connected = core.copy()
    source = core.astype(np.uint8)
    radius = 8
    for angle in np.arange(16)*np.pi/16:
        dx, dy = round(radius*np.cos(angle)), round(radius*np.sin(angle))
        line = np.zeros((2*radius+1, 2*radius+1), np.uint8)
        cv2.line(line, (radius-dx, radius-dy), (radius+dx, radius+dy), 1)
        closed = cv2.morphologyEx(source, cv2.MORPH_CLOSE, line,
                                  borderType=cv2.BORDER_CONSTANT, borderValue=0)
        added = (closed > 0) & ~core
        crosswise = np.zeros_like(core)
        for direction, stroke in strokes:
            if abs(np.sin(direction-angle)) >= .7:
                crosswise |= stroke
        blocked = added & (crosswise | ~permitted)
        if blocked.any():
            added &= cv2.dilate(blocked.astype(np.uint8), line) == 0
        connected |= added & permitted & ~crosswise
    return connected


def _trim_bright_rim(mask, raw, target):
    """Remove exposed neighbouring paper without breaking the recovered strip."""
    current = np.pad(mask, 1)
    light = np.pad(raw[:, :, 0], 1)
    difference = np.pad(np.linalg.norm(raw[:, :, 1:]-target[1:], axis=2), 1)
    removable = (light > target[0]+13) & (difference > 7)
    y, x = np.nonzero(removable)
    phase = 2*(y % 2)+x % 2
    offsets = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))
    for _ in range(4):
        changed = 0
        for group in range(4):
            rows, cols = y[phase == group], x[phase == group]
            ring = np.stack([current[rows+dy, cols+dx] for dy, dx in offsets])
            faces = ring[::2].sum(axis=0)
            transitions = np.count_nonzero(ring != np.roll(ring, 1, axis=0), axis=0)
            # Two contiguous arcs retain connectivity and existing holes.
            # Preserve endpoints and one-pixel-wide bridges.
            take = current[rows, cols] & (transitions == 2) & (faces >= 2) & (faces <= 3)
            current[rows[take], cols[take]] = False
            changed += int(take.sum())
        if not changed:
            break
    return current[1:-1, 1:-1]


def _strip_proposals(objects, area, ids, raw, valid, target):
    light = raw[:, :, 0]
    distance = np.hypot(raw[:, :, 1]-target[1], raw[:, :, 2]-target[2])
    core = valid & (distance < 7) & (light > target[0]-53) & (light < target[0]+13)
    weak = valid & (distance < 13) & (light > target[0]-90) & (light < target[0]+21)
    near = cv2.dilate(weak.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    # A dark crossing can be text or a genuine separator. Keep it intact when
    # it extends across the strip as a sustained straight ink stroke.
    ink = ((light < target[0]-75) &
           (cv2.GaussianBlur(light, (0, 0), 3).astype(float)-light > 20)).astype(np.uint8)
    # A compact printed dot can cover the pigment completely. Keep long,
    # slender strokes available as separators, while allowing a short aligned
    # link between visible samples on either side of a compact mark.
    contrast = cv2.GaussianBlur(light, (0, 0), 3).astype(np.float32)-light
    marks, total = ndimage.label((light < 130) & (contrast > 25))
    mark_sizes = np.bincount(marks.ravel())
    compact = np.zeros(total+1, bool)
    for ident, box in enumerate(ndimage.find_objects(marks), 1):
        if box is None or not 5 <= mark_sizes[ident] <= 180:
            continue
        height, width = (axis.stop-axis.start for axis in box)
        compact[ident] = max(height, width) <= 26 and max(height, width) <= 5*min(height, width)
    printed = compact[marks]
    near |= printed
    ink[printed] = 0
    strokes = []
    barrier = np.zeros_like(valid)
    for angle in np.arange(8)*np.pi/8:
        dx, dy = round(6*np.cos(angle)), round(6*np.sin(angle))
        line = np.zeros((13, 13), np.uint8)
        cv2.line(line, (6-dx, 6-dy), (6+dx, 6+dy), 1)
        stroke = cv2.morphologyEx(ink, cv2.MORPH_OPEN, line) > 0
        # The tip of a line ending on a bank does not span the strip. Only its
        # interior can veto a link, with visible ink extending on both sides.
        dx, dy = round(4*np.cos(angle)), round(4*np.sin(angle))
        tip = np.zeros((9, 9), np.uint8)
        cv2.line(tip, (4-dx, 4-dy), (4+dx, 4+dy), 1)
        through = cv2.erode(stroke.astype(np.uint8), tip) > 0
        strokes.append((angle, through))
        barrier |= stroke
    connected, count = ndimage.label(_link_aligned(core, near & valid, strokes))
    votes = np.zeros(count+1, dtype=int)
    for i in ids:
        hits = np.bincount(connected[(objects == i) & core], minlength=count+1)
        hits[0] = 0
        if hits.max() >= .18 * area[i]:
            votes[hits.argmax()] += 1

    bounds = ndimage.find_objects(connected)
    for k in np.flatnonzero(votes >= 2):
        box = tuple(slice(max(0, s.start-6), min(n, s.stop+6))
                    for s, n in zip(bounds[k-1], objects.shape))
        main = connected[box] == k
        span = np.linalg.norm(np.ptp(np.column_stack(np.nonzero(main)), axis=0))
        if main.sum() > 16 * span:
            continue
        local = objects[box]
        allowed = valid[box] & ~barrier[box]
        limit = ndimage.binary_dilation(main, iterations=2) & allowed
        seeds = np.zeros(main.shape, np.uint8)
        seeds[~limit] = 2
        seeds[main] = 1
        expanded = watershed(255-light[box], seeds, mask=allowed) == 1
        # Beside a large existing fill, avoid widening an inferred gap into it.
        # Only expand there next to pigment actually seen in the source.
        near_pigment = ndimage.binary_dilation(main & core[box])
        expanded &= (area[local] < 1500) | near_pigment
        hits = np.bincount(local[main], minlength=len(area))
        matched = (hits > .45*area) & (area < 1800)
        # Grow only into nearby confirmed pixels of a mostly recovered fragment.
        support = ndimage.binary_dilation(main, iterations=5, mask=allowed) & matched[local]
        recovered = ndimage.binary_propagation(main, mask=main | expanded | support)
        yield box, _trim_bright_rim(recovered, raw[box], target)


def restore_tinted_strips(objects, units, area, paper, raw):
    valid = objects != 0
    proposals = []
    occupied = np.zeros(objects.shape, np.uint16)
    for unit, ids in _strip_anchors(objects, units, area).items():
        if len(ids) < 2:
            continue
        target = np.median(paper[ids], axis=0)
        stable = [i for i in ids if np.linalg.norm(paper[i, 1:]-target[1:]) <= 4
                  and abs(paper[i, 0]-target[0]) <= 14]
        if len(stable) < 2:
            continue
        for box, mask in _strip_proposals(objects, area, stable, raw, valid, target):
            occupied[box] += mask
            proposals.append((unit, box, mask))
    accepted = [(unit, box, mask) for unit, box, mask in proposals
                if not np.any(occupied[box][mask] > 1)]
    if not accepted:
        return objects, units
    result = objects.copy()
    for i, (_, box, mask) in enumerate(accepted, len(units)):
        result[box][mask] = i
    return result, np.r_[units, np.asarray([unit for unit, _, _ in accepted], dtype=units.dtype)]
