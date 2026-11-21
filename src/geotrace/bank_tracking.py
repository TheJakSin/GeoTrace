"""Follow pale strips across changes in the polygons bordering their two sides."""
from collections import defaultdict, deque

import cv2
import numpy as np
from scipy import ndimage, signal


# Five transverse sections at two scales. Sampling coordinates are in overview pixels.
_ANGLES = np.arange(16) * np.pi / 8
_DIRECTIONS = np.column_stack((np.sin(_ANGLES), np.cos(_ANGLES)))
_OFFSETS = np.arange(-23, 24)
_STATIONS = np.arange(-2, 3)


def _section(line, mask, valid, evidence):
    """Find the strip containing or nearest the centre of a single section."""
    runs = np.flatnonzero(np.diff(np.r_[False, mask & valid, False])).reshape(-1, 2)
    if not len(runs):
        return None
    center = len(line) // 2
    lo, hi = min(runs, key=lambda pair: max(pair[0]-center, center+1-pair[1], 0))
    if lo == 0 or hi == len(line) or hi-lo < 3 or max(lo-center, center+1-hi) > 4:
        return None
    if not valid[lo-1] or not valid[hi] or not line[lo-1] or not line[hi]:
        return None
    return (lo, hi, line[lo-1], line[hi], max(evidence[lo-1:lo+1]), max(evidence[hi-1:hi+1]))


def _follows_banks(sections, lines, first, second):
    lower, upper = sections[:, 0], sections[:, 1]
    width = upper - lower
    if width.max() > 32 or width.max() > 2.8*width.min():
        return False
    sides = sections[:, :2]
    if np.max(abs(np.diff(sides, axis=0))) > 9 or np.max(abs(np.diff(sides, n=2, axis=0))) > 9:
        return False
    # At least one side continues at three of the four transitions. Which side
    # supplies the evidence can change, provided both outlines stay aligned.
    same_bank = (sections[:-1, 2:4] == sections[1:, 2:4]).any(axis=1)
    if same_bank.sum() < 3 or sections[:, 4:].mean(axis=0).min() < .55:
        return False
    inside = (np.arange(lines.shape[1]) >= lower[:, None]) & (np.arange(lines.shape[1]) < upper[:, None])
    return ((lines[:2] == first) & inside[:2]).any() and ((lines[-2:] == second) & inside[-2:]).any()


def _tracks_contact(first, second, center, spacing, regions, boundary, paper, size, hatch):
    radius = 30
    y, x = np.rint(center).astype(int)
    y0, x0 = max(y-radius, 0), max(x-radius, 0)
    window = np.s_[y0:y+radius+1, x0:x+radius+1]
    labels, evidence = regions[window], boundary[window]
    center = center - (y0, x0)
    ids, inverse = np.unique(labels, return_inverse=True)
    colour = paper[first if paper[first, 0] >= paper[second, 0] else second]
    local = paper[ids]
    fits = ((ids != 0) & (hatch[ids] < .4) & (local[:, 0] >= colour[0]-29)
            & (np.linalg.norm(local[:, 1:]-colour[1:], axis=1) < 10)
            & ((size[ids] < 700) | (np.linalg.norm(local-colour, axis=1) <= 8)))
    mask = (fits | (ids == first) | (ids == second))[inverse.reshape(labels.shape)]
    delta = np.moveaxis(np.indices(labels.shape), 0, -1) - center
    near = np.square(delta).sum(axis=2) < 16**2
    a, b = near & (labels == first), near & (labels == second)
    if not a.any() or not b.any():
        return False
    aim = delta[b].mean(axis=0) - delta[a].mean(axis=0)
    aim /= max(1., np.linalg.norm(aim))
    for direction in _DIRECTIONS[_DIRECTIONS @ aim >= .55]:
        normal = np.array([-direction[1], direction[0]])
        grid = (center[:, None, None] + direction[:, None, None] * (spacing*_STATIONS)[None, :, None]
                + normal[:, None, None] * _OFFSETS)
        iy, ix = np.rint(grid).astype(int)
        valid = (iy >= 0) & (ix >= 0) & (iy < labels.shape[0]) & (ix < labels.shape[1])
        iy, ix = iy.clip(0, labels.shape[0]-1), ix.clip(0, labels.shape[1]-1)
        lines = labels[iy, ix]
        sections = [_section(*row) for row in zip(lines, mask[iy, ix], valid, evidence[iy, ix])]
        if None not in sections and _follows_banks(np.asarray(sections), lines, first, second):
            return True
    return False


def _single_ink_line(mask, light):
    """Check whether apparent hatching in a narrow strip is one lengthwise stroke."""
    coords = np.column_stack(np.nonzero(mask))
    origin = coords.mean(axis=0)
    variance, axes = np.linalg.eigh(np.cov(coords.T))
    if variance[1] < 2.5 * max(.25, variance[0]):
        return False
    if ndimage.distance_transform_edt(np.pad(mask, 1)).max() > 7.5:
        return False
    along, across = axes[:, 1], axes[:, 0]
    t, s = (coords - origin) @ along, (coords - origin) @ across
    stations = np.arange(np.ceil(t.min()), np.floor(t.max()) + 1)
    offsets = np.arange(np.floor(s.min()) - 1, np.ceil(s.max()) + 2)
    grid = (origin[:, None, None] + along[:, None, None] * stations[None, :, None]
            + across[:, None, None] * offsets)
    # Use a consistent nearest-pixel tie break on half-pixel centred strips.
    y, x = np.floor(grid + .5).astype(int)
    in_bounds = (y >= 0) & (x >= 0) & (y < mask.shape[0]) & (x < mask.shape[1])
    y, x = y.clip(0, mask.shape[0]-1), x.clip(0, mask.shape[1]-1)
    inside = mask[y, x] & in_bounds
    core = ndimage.binary_erosion(mask)[y, x] & in_bounds
    used = core.sum(axis=1) >= 2
    if used.sum() < 4:
        return False

    # Only count a stroke with lighter interior pixels on both sides.
    values = light[y, x].astype(float)
    padded = np.pad(np.where(core, values, 0), ((0, 0), (3, 3)))
    left = np.maximum.reduce([padded[:, i:i+len(offsets)] for i in (0, 1, 2)])
    right = np.maximum.reduce([padded[:, i:i+len(offsets)] for i in (4, 5, 6)])
    ink = core & (values < 190) & (np.minimum(left, right) - values > 18)
    strokes = np.count_nonzero(np.diff(np.pad(ink, ((0, 0), (1, 1))), axis=1), axis=1) // 2
    if (strokes[used] > 1).mean() > .15:
        return False

    # Oblique or transverse hatching repeats along the axis of the strip.
    repeated = 0
    rows = np.arange(len(stations))
    spans = np.flatnonzero(np.diff(np.r_[False, used, False])).reshape(-1, 2)
    first = inside.argmax(axis=1)
    last = len(offsets) - 1 - inside[:, ::-1].argmax(axis=1)
    for fraction in (.25, .5, .75):
        cols = np.rint(first + fraction * (last-first)).astype(int)
        profile = values[rows, cols]
        periodic = False
        for lo, hi in spans:
            peaks = signal.find_peaks(-profile[lo:hi], prominence=18, distance=2)[0]
            steps = np.diff(peaks)
            longer = np.maximum(steps[1:], steps[:-1])
            regular = (longer <= 9) & (np.minimum(steps[1:], steps[:-1]) >= .7 * longer)
            if np.any(regular[:-1] & regular[1:]):
                return False
            periodic |= regular.any()
        repeated += int(periodic)
    return repeated < 2


def _paper_opening(labels, raw, a, b, paper, y, x, ny, nx):
    """Require a connected patch of exposed fill crossing several adjacent faces."""
    y0, x0 = max(0, y.min()-8), max(0, x.min()-8)
    roi = np.s_[y0:ny.max()+9, x0:nx.max()+9]
    labels, raw = labels[roi], raw[roi]
    chroma = (paper[a, 1:] + paper[b, 1:]) * .5
    exposed = (((labels == a) | (labels == b)) & (raw[:, :, 0] >= min(paper[a, 0], paper[b, 0])-37)
               & (np.linalg.norm(raw[:, :, 1:]-chroma, axis=2) <= 10))
    y, ny, x, nx = y-y0, ny-y0, x-x0, nx-x0
    open_faces = exposed[y, x] & exposed[ny, nx]
    if open_faces.sum() < 3:
        return False
    # Rasterize both ends of each clear face. Eight-connected face pixels
    # accommodate staircase-shaped cuts, but the fill itself must be four-connected.
    face = np.zeros_like(exposed)
    face[y[open_faces], x[open_faces]] = True
    face[ny[open_faces], nx[open_faces]] = True
    groups, n = ndimage.label(face, structure=np.ones((3, 3)))
    components, n = ndimage.label(exposed)
    fy, fx = y[open_faces], x[open_faces]
    pairs, count = np.unique(np.column_stack((groups[fy, fx], components[fy, fx])), axis=0, return_counts=True)
    patches = pairs[count >= 3, 1]
    if not patches.size:
        return False
    deep_a = (labels == a) & (ndimage.distance_transform_edt(labels != b) >= 3)
    deep_b = (labels == b) & (ndimage.distance_transform_edt(labels != a) >= 3)
    area_a = np.bincount(components[deep_a], minlength=n+1)
    area_b = np.bincount(components[deep_b], minlength=n+1)
    crosses = (area_a >= 4) & (area_b >= 4)
    crosses[0] = False
    return crosses[patches].any()


def _shortest_route(graph, allowed, start, end):
    """Return a unique shortest chain. Leave equally short alternatives separate."""
    distance, ways, parent = {start: 0}, {start: 1}, {}
    queue = deque([start])
    while queue:
        k = queue.popleft()
        for j in graph[k] & allowed:
            hops = distance[k] + 1
            if j not in distance:
                distance[j], ways[j], parent[j] = hops, ways[k], k
                queue.append(j)
            elif distance[j] == hops:
                ways[j] = min(2, ways[j] + ways[k])
    if ways.get(end) != 1:
        return []
    path = [end]
    while path[-1] != start:
        path.append(parent[path[-1]])
    return path[::-1]


def _join_routes(neighbours, contacts, vetoes, size, units, paper, lookup):
    ends = set(np.flatnonzero((size >= 1500) & (paper[:, 0] >= 235)))
    seen = set()
    routes = []
    for seed in neighbours.keys() - ends:
        if seed in seen:
            continue
        interior, anchors = set(), set()
        stack = [seed]
        while stack:
            k = stack.pop()
            if k in interior:
                continue
            interior.add(k)
            anchors.update(neighbours[k] & ends)
            stack.extend(neighbours[k] - ends - interior)
        seen.update(interior)
        if len(anchors) != 2:
            continue
        a, b = sorted(anchors)
        if units[a] != units[b] or (a, b) in contacts or np.linalg.norm(paper[a]-paper[b]) > 6:
            continue
        path = _shortest_route(neighbours, interior | anchors, a, b)
        if len(path) < 4:
            continue
        route = set(path)
        if any(j in route for k in path for j in vetoes[k]):
            continue
        joins = np.array([contacts[min(i, j), max(i, j)] for i, j in zip(path, path[1:])])
        start, stop = joins[0], joins[-1]
        along = stop - start
        length = np.linalg.norm(along)
        if length < 36:
            continue
        along /= length
        across = np.array([-along[1], along[0]])
        offsets = joins - start
        if np.max(abs(offsets @ across)) > max(12, .18*length):
            continue
        if np.any(np.diff(offsets @ along) < -1):
            continue
        routes.append((route, {a: start, b: stop}))

    # Independent branches can enter opposite parts of a large polygon.
    # A shared mouth, competing paths or a separator anywhere in the proposed
    # union makes that whole group ambiguous.
    through = defaultdict(set)
    for i, (route, _) in enumerate(routes):
        for k in route:
            through[k].add(i)
    pending = set(range(len(routes)))
    joined = np.arange(len(size), dtype=lookup.dtype)
    while pending:
        stack = [pending.pop()]
        group = set()
        mouths = defaultdict(list)
        pairs = set()
        ambiguous = False
        while stack:
            i = stack.pop()
            route, entrances = routes[i]
            ambiguous |= bool((group & route) - entrances.keys())
            pair = tuple(sorted(entrances))
            ambiguous |= pair in pairs
            pairs.add(pair)
            for k, point in entrances.items():
                ambiguous |= any(np.linalg.norm(point-other) < 46 for other in mouths[k])
                mouths[k].append(point)
            group.update(route)
            for k in route:
                connected = through[k] & pending
                stack.extend(connected)
                pending.difference_update(connected)
        if ambiguous or any(vetoes[k] & group for k in group):
            continue
        owner = max(mouths, key=lambda k: (size[k], -k))
        if all(np.linalg.norm(paper[k]-paper[owner]) <= 6 for k in mouths):
            joined[list(group)] = owner
    return joined[lookup]


def track_banks(lookup, units, regions, original_size, original_paper, original_hatch,
                raw, boundary, positions, vertical, left, right, inverse):
    """Consider existing faces only. Retain holes and the exact project footprint."""
    count = len(lookup)
    size = np.bincount(lookup, weights=original_size, minlength=count)
    if np.count_nonzero(size[1:] >= 1500) < 2:
        return lookup
    weight = original_size / np.maximum(size[lookup], 1)
    paper = np.column_stack([np.bincount(lookup, weights=weight*axis, minlength=count)
                             for axis in original_paper.T])
    hatch = np.bincount(lookup, weights=weight*original_hatch, minlength=count)
    a, b = lookup[left], lookup[right]
    pair_codes = np.minimum(a, b).astype(np.int64)*count + np.maximum(a, b)
    codes = pair_codes[inverse]
    order = np.argsort(codes, kind='stable')
    pairs, starts, lengths = np.unique(codes[order], return_index=True, return_counts=True)
    grouped = lookup[regions]
    light = raw[:, :, 0]
    boxes = ndimage.find_objects(grouped)
    for k in np.flatnonzero((hatch > .30) & (size >= 35) & (size < 1500) & (paper[:, 0] >= 231)):
        if k == 0:
            continue
        box = boxes[k-1]
        if _single_ink_line(grouped[box] == k, light[box]):
            hatch[k] = 0
    darker = cv2.erode(light, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)))
    neighbours = defaultdict(set)
    contacts = {}
    vetoes = defaultdict(set)
    for code, lo, n in zip(pairs, starts, lengths):
        a, b = divmod(int(code), count)
        if a == b:
            continue
        # Keep adjacency even for vetoed faces, to prevent bypassing a direct
        # boundary via a second, longer route.
        contacts[a, b] = None
        same_paper = (min(paper[a, 0], paper[b, 0]) >= 218
                      and max(paper[a, 0], paper[b, 0]) >= 231
                      and abs(paper[a, 0]-paper[b, 0]) <= 26
                      and np.linalg.norm(paper[a, 1:]-paper[b, 1:]) <= 9
                      and max(hatch[a], hatch[b]) <= .30)
        if not same_paper:
            vetoes[a].add(b)
            vetoes[b].add(a)
            continue
        idx = order[lo:lo+n]
        y, x = np.divmod(positions[idx], regions.shape[1])
        dy = vertical[idx].astype(int)
        ny, nx = y+dy, x+1-dy
        support = (np.maximum(boundary[y,x], boundary[ny,nx]) > .65).mean()
        dark = (np.minimum(light[y,x], light[ny,nx]) < 155).mean()
        near_ink = (np.minimum(darker[y,x], darker[ny,nx]) < 150).mean()
        # A sideways contact can pass through lettering even when the route around
        # it is clear. Preserve an actual separator, including one just off the face.
        eligible = 5 <= n <= 105 and min(size[a], size[b]) >= 35
        inked = dark > .48 or near_ink > .75
        open_ink = (inked and eligible and support <= .83 and dark <= .85
                    and _paper_opening(grouped, raw, a, b, paper, y, x, ny, nx))
        if n >= 3 and (support > .83 or dark > .85 or (near_ink > .75 and support > .4 and not open_ink)):
            vetoes[a].add(b)
            vetoes[b].add(a)
            continue
        if not eligible or (inked and not open_ink):
            continue
        xy = np.column_stack(((y+ny)*.5, (x+nx)*.5))
        center = xy.mean(axis=0)
        args = grouped, boundary, paper, size, hatch
        if not _tracks_contact(a, b, center, 6, *args):
            axis = np.linalg.eigh(np.cov(xy.T))[1][:, 1]
            ordered = xy[np.argsort(xy @ axis)]
            samples = (ordered[:n//2].mean(axis=0), ordered[n//2:].mean(axis=0), center)
            if not any(_tracks_contact(a, b, point, 4, *args) for point in samples):
                continue
        contacts[a, b] = center
        neighbours[a].add(b)
        neighbours[b].add(a)
    return _join_routes(neighbours, contacts, vetoes, size, units, paper, lookup)
