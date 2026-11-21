"""Reconnect short interruptions in a pale strip bounded by the same two neighbouring regions."""
from collections import defaultdict
from itertools import combinations
from typing import NamedTuple

import numpy as np
from scipy import ndimage


class Contact(NamedTuple):
    length: int
    midpoint: np.ndarray
    strength: float


def contact_graph(lookup, left, right, length, strength, midpoint):
    """Combine all faces of a contact after the previous region merges."""
    count = len(lookup)
    a, b = lookup[left], lookup[right]
    keep = a != b
    pairs = np.minimum(a[keep], b[keep]).astype(np.int64)*count + np.maximum(a[keep], b[keep])
    keys, inverse = np.unique(pairs, return_inverse=True)
    weights = length[keep]
    total = np.bincount(inverse, weights=weights)
    centers = np.column_stack([np.bincount(inverse, weights=weights*axis[keep])/total
                               for axis in midpoint.T])
    support = np.bincount(inverse, weights=weights*strength[keep])/total
    graph = defaultdict(dict)
    for key, n, center, fraction in zip(keys, total, centers, support):
        a, b = divmod(int(key), count)
        graph[a][b] = graph[b][a] = Contact(int(n), center, fraction)
    return graph


def cosine(a, b):
    return np.dot(a, b) / max(.01, np.linalg.norm(a)*np.linalg.norm(b))


def bounded_ink_mark(light, regions, coords, banks, across, paper_light):
    """Look for a solid ink core, then check for a line continuing into both banks."""
    low = coords.min(axis=0) - 2
    high = coords.max(axis=0) + 3
    if np.any(low < 0) or np.any(high > light.shape):
        return False
    y0, x0 = low
    y1, x1 = high
    cut = .65 * paper_light
    ink = light[y0:y1, x0:x1] < cut
    disk = np.indices((5, 5)) - 2
    core = ndimage.binary_erosion(ink, structure=np.sum(disk**2, axis=0) <= 4)
    if not core[tuple((coords-low).T)].any():
        return False
    height, width = light.shape
    for angle in np.arange(8)*np.pi/8:
        direction = np.array([np.sin(angle), np.cos(angle)])
        if abs(cosine(direction, across)) < .75:
            continue
        offsets = np.rint(np.arange(-10, 11)[:, None]*direction).astype(int)
        y, x = (coords[:, None, :] + offsets).transpose(2, 0, 1)
        inside = (y >= 0) & (y < height) & (x >= 0) & (x < width)
        y, x = np.clip(y, 0, height-1), np.clip(x, 0, width-1)
        first, last = regions[y[:, 0], x[:, 0]], regions[y[:, -1], x[:, -1]]
        spans = ((first == banks[0]) & (last == banks[1])) | ((first == banks[1]) & (last == banks[0]))
        dark = light[y, x] < cut
        if np.any(spans & inside.all(axis=1) & (dark.sum(axis=1) >= 19)):
            return False
    return True


def supported_path(path, graph, units, paper, banks, geometry, light, regions):
    start, end = path[0], path[-1]
    if not units[start] or units[start] != units[end] or end in graph[start]:
        return False
    if np.linalg.norm(paper[start] - paper[end]) > 6:
        return False
    colour = (paper[start] + paper[end]) / 2
    if any(np.linalg.norm(paper[n, 1:] - colour[1:]) < 6 for n in banks):
        return False

    hidden = None
    for k in path[1:-1]:
        if abs(paper[k, 0]-colour[0]) <= 18 and np.linalg.norm(paper[k, 1:]-colour[1:]) <= 5:
            continue
        # One compact ink mark can hide the paper if another piece still
        # shows the fill. Elongated slivers and transverse lines stay separate.
        if hidden is not None or len(path) < 4 or colour[0]-paper[k, 0] < 75:
            return False
        _, across, coords = geometry[k]
        if len(coords) > 42 or np.ptp(coords, axis=0).max() >= 10:
            return False
        variance = np.linalg.eigvalsh(np.cov(coords.T))
        if variance[1] > 4*max(.15, variance[0]):
            return False
        if not bounded_ink_mark(light, regions, coords, banks, across, colour[0]):
            return False
        hidden = k
    return all(hidden in (a, b) or graph[a][b].strength <= .70 for a, b in zip(path, path[1:]))


def compact_ink_path(path, graph, units, paper, banks, geometry, light, regions):
    """Check several touching fragments as one bounded printed mark."""
    start, end = path[0], path[-1]
    if not units[start] or units[start] != units[end] or end in graph[start]:
        return False
    if np.linalg.norm(paper[start]-paper[end]) > 6:
        return False
    colour = (paper[start]+paper[end]) / 2
    if any(np.linalg.norm(paper[k, 1:]-colour[1:]) < 6 for k in banks):
        return False
    coords = np.vstack([geometry[k][2] for k in path[1:-1]])
    extent = np.ptp(coords, axis=0)+1
    if len(coords) > 150 or extent.max() > 24:
        return False
    minor, major = np.linalg.eigvalsh(np.cov(coords.T))
    if major > 8*max(.15, minor):
        return False
    if (light[tuple(coords.T)] < .65*colour[0]).mean() < .6:
        return False
    across = sum((geometry[k][1] for k in path[1:-1]), np.zeros(2))
    return bounded_ink_mark(light, regions, coords, banks, across, colour[0])


def corridor_paths(graph, size, units, paper, hatching, regions, light):
    banks = defaultdict(set)
    for k in np.flatnonzero((size >= 4) & (size <= 450) & (hatching < .25)):
        if k:
            sides = [n for n in graph[k] if size[n] >= max(500, 2*size[k])]
            for pair in combinations(sorted(sides), 2):
                banks[pair].add(k)

    boxes = ndimage.find_objects(regions)
    paths = set()
    for (a, b), fragments in banks.items():
        ends = {k for k in graph[a].keys() & graph[b].keys()
                if size[k] >= 500 and paper[k, 0] >= 228 and hatching[k] < .2
                and min(graph[k][a].length, graph[k][b].length) >= 6}
        if len(ends) < 2:
            continue
        geometry = {}
        for k in fragments:
            box = boxes[k-1]
            if max(sl.stop-sl.start for sl in box) > 48:
                continue
            coords = np.column_stack(np.nonzero(regions[box] == k)) + [sl.start for sl in box]
            center = coords.mean(axis=0)
            side_a, side_b = graph[k][a].midpoint - center, graph[k][b].midpoint - center
            if cosine(side_a, side_b) <= -.25:
                geometry[k] = center, side_b-side_a, coords

        # Explore only touching pieces. Even where many branches have the
        # same banks, distant ends never enter a pairwise comparison.
        local = geometry.keys() | ends
        stack = [(start, k) for k in geometry for start in graph[k].keys() & ends
                 if graph[k][start].length >= 1]
        while stack:
            path = stack.pop()
            prev = path[-1]
            center, across, _ = geometry[prev]
            before = graph[prev][path[-2]].midpoint - center
            for k in graph[prev].keys() & local:
                edge = graph[prev][k]
                if k in path or (edge.length < 2 and k not in ends):
                    continue
                after = edge.midpoint - center
                if cosine(before, after) > -.25 or abs(cosine(after-before, across)) > .80:
                    continue
                step = path + (k,)
                if k in ends:
                    if k <= path[0]:
                        continue
                    regular = (all(graph[i][j].length >= 2 for i, j in zip(step, step[1:]))
                               and supported_path(step, graph, units, paper, (a, b), geometry, light, regions))
                    if regular or compact_ink_path(step, graph, units, paper, (a, b), geometry, light, regions):
                        paths.add(step)
                elif len(step) <= 4:
                    stack.append(step)
    return paths


def join_corridors(lookup, units, regions, size, paper, hatch_fraction, light, graph):
    count = len(lookup)
    area = np.bincount(lookup, weights=size, minlength=count)
    if np.count_nonzero(area[1:] >= 500) < 4:
        return lookup
    weights = size / np.maximum(1, area[lookup])
    colour = np.column_stack([np.bincount(lookup, weights=weights*axis, minlength=count)
                              for axis in paper.T])
    hatch = np.bincount(lookup, weights=weights*hatch_fraction, minlength=count)
    paths = corridor_paths(graph, area, units, colour, hatch, lookup[regions], light)
    # Competing routes through the same fragment need manual interpretation.
    usage = defaultdict(int)
    for path in paths:
        for k in path:
            usage[k] += 1
    joined = np.arange(count, dtype=lookup.dtype)
    for path in paths:
        if all(usage[k] == 1 for k in path):
            owner = max((path[0], path[-1]), key=lambda k: area[k])
            joined[list(path)] = owner
    return joined[lookup]
