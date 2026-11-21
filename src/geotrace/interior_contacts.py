"""Recover long inked contacts between coherent tones inside one colour group."""
from collections import deque

import cv2
import numpy as np
from scipy import ndimage
from skimage.segmentation import watershed

from .contacts import sample_contact_flanks


def _tone_partition(mask, lab, boundary):
    interior = ndimage.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1] > 3
    if interior.sum() < 300:
        return None
    weight = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 3)
    smooth = np.stack([cv2.GaussianBlur(np.where(mask, channel, 0).astype(np.float32), (0, 0), 3)
                       / np.maximum(weight, 1e-5) for channel in lab.transpose(2, 0, 1)], axis=2)
    data = smooth[interior]
    samples = data[::max(1, len(data)//12000)].copy()
    cv2.setRNGSeed(42)
    _, _, centers = cv2.kmeans(samples, 2, None,
        (cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER, 30, .1), 3, cv2.KMEANS_PP_CENTERS)
    if not 3 <= np.linalg.norm(centers[0]-centers[1]) <= 10:
        return None
    # Brightness alone can vary with printing density. Require a colour change.
    if np.linalg.norm(centers[0, 1:]-centers[1, 1:]) < 3:
        return None
    distance = np.linalg.norm(smooth[:, :, None]-centers, axis=3)
    delta = distance[:, :, 0]-distance[:, :, 1]
    seeds = np.where(interior & (boundary < .35) & (abs(delta) > 1.5),
                     np.where(delta > 0, 2, 1), 0).astype(np.uint8)
    for tone in (1, 2):
        components, _ = ndimage.label(seeds == tone)
        area = np.bincount(components.ravel())
        keep = area >= 80
        keep[0] = False
        seeds[(seeds == tone) & ~keep[components]] = 0
        if not np.any(seeds == tone):
            return None
    parts = watershed(boundary, seeds, mask=mask)
    minimum = max(256, .18*mask.sum())
    for tone in (1, 2):
        components, _ = ndimage.label(parts == tone)
        areas = np.bincount(components.ravel())[1:]
        if areas.sum() < minimum or np.count_nonzero(areas >= 48) != 1:
            return None
    return parts


def _supported_contact(parts, lab, light, boundary, evidence):
    available = dark = length = 0
    support, continuity = [], []
    nearby_boundary = ndimage.maximum_filter(boundary, 3)
    nearby_line = ndimage.maximum_filter(evidence, 3)
    for axis, (dy, dx) in enumerate(((1, 0), (0, 1))):
        a = parts[:parts.shape[0]-dy, :parts.shape[1]-dx]
        b = parts[dy:, dx:]
        y, x = np.nonzero((a != b) & (a > 0) & (b > 0))
        probes, _, trough = sample_contact_flanks(parts, lab, light, y, x, axis)
        available += int(probes.sum())
        dark += int(trough.sum())
        length += len(y)
        support.extend(np.maximum(nearby_boundary[y, x], nearby_boundary[y+dy, x+dx]))
        continuity.extend(np.maximum(nearby_line[y, x], nearby_line[y+dy, x+dx]))
    return (length >= 40 and available >= .5*length and dark >= .6*available
            and np.mean(np.asarray(support) > .45) >= .85 and np.median(continuity) >= .25)


def split_interior_contacts(objects, classes, prepared, check=None):
    """Resolve several contacts with independent tone, geometry and ink evidence.

    Recheck each accepted child in its own tight bounds. A three-level tree
    bounds the work to seven proposals per original object; every proposal
    must independently pass the same contact checks as the first split.
    """
    sizes = np.bincount(objects.ravel(), minlength=len(classes))
    bounds = ndimage.find_objects(objects)
    light = cv2.cvtColor(prepared.raster.rgb, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)
    result = objects.copy()
    units = list(classes)
    pending = deque((int(ident), bounds[ident-1], 0)
                    for ident in np.flatnonzero(sizes >= 1800) if ident)
    while pending:
        if check is not None:
            check()
        ident, roi, depth = pending.popleft()
        mask = result[roi] == ident
        if mask.sum() < 1800:
            continue
        parts = _tone_partition(mask, prepared.lab[roi], prepared.boundary[roi])
        if parts is None or not _supported_contact(parts, prepared.lab[roi], light[roi],
                                                   prepared.boundary[roi], prepared.line_evidence[roi]):
            continue
        child = len(units)
        result[roi][parts == 2] = child
        units.append(units[ident])
        if depth < 2:
            for tone, owner in ((1, ident), (2, child)):
                y, x = np.nonzero(parts == tone)
                if len(y) < 1800:
                    continue
                child_roi = (slice(roi[0].start+int(y.min()), roi[0].start+int(y.max())+1),
                             slice(roi[1].start+int(x.min()), roi[1].start+int(x.max())+1))
                pending.append((owner, child_roi, depth+1))
    return result, np.asarray(units, dtype=classes.dtype)
