"""Shared CPU-only reconstruction of colors beneath printed ink."""
import cv2
import numpy as np
from scipy import ndimage


def reconstruct_colors(rgb, valid):
    """Keep the source, detected ink and pre-filter reconstruction separate."""
    if not valid.any():
        raise ValueError('В области нет данных.')
    # Fill transparency before filtering so black frame cannot create a false boundary.
    indices = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = rgb[tuple(indices)]
    lab = cv2.cvtColor(filled, cv2.COLOR_RGB2LAB)
    light = lab[:, :, 0]
    local_light = ndimage.percentile_filter(light, 70, size=9)
    ink = (light.astype(np.int16) < local_light.astype(np.int16) - 24) & valid
    reconstructed = (cv2.inpaint(filled, ink.astype(np.uint8)*255, 3, cv2.INPAINT_TELEA)
                     if ink.any() and (~ink & valid).any() else filled)
    return filled, ink, reconstructed


def clean_colors(rgb, valid):
    """Local bright-paper reconstruction; may erase narrow genuine dark units."""
    return cv2.medianBlur(reconstruct_colors(rgb, valid)[2], 5)


def consensus_colors(filled, ink, valid, reconstructed, check=None):
    """Recover ink-covered fill only from a dominant agreement of opposing probes.

    Palette estimation and boundary evidence use the original reconstruction.
    Unsupported or competing colours retain that reconstruction. Probe arrays
    are bounded independently of image size.
    """
    y_all, x_all = np.nonzero(ink & valid)
    result = reconstructed.copy()
    height, width = valid.shape
    accepted = 0
    for start in range(0, len(y_all), 65536):
        if check is not None:
            check()
        y, x = y_all[start:start+65536], x_all[start:start+65536]
        count = len(y)
        candidates, fits, costs = [], [], []
        for dy, dx in ((1, 0), (0, 1), (1, 1), (1, -1)):
            sides, lengths = [], []
            for sign in (-1, 1):
                value = np.zeros((count, 3), np.uint8)
                distance = np.zeros(count, np.uint8)
                reachable = np.ones(count, bool)
                for radius in range(1, 7):
                    sy, sx = y+sign*radius*dy, x+sign*radius*dx
                    inside = (sy >= 0) & (sx >= 0) & (sy < height) & (sx < width)
                    sy, sx = sy.clip(0, height-1), sx.clip(0, width-1)
                    reachable &= inside & valid[sy, sx]
                    if dy and dx:
                        py, px = (sy-sign*dy).clip(0, height-1), (sx-sign*dx).clip(0, width-1)
                        reachable &= valid[py, sx] | valid[sy, px]
                    take = reachable & ~ink[sy, sx] & (distance == 0)
                    value[take] = filled[sy[take], sx[take]]
                    distance[take] = radius
                sides.append(value)
                lengths.append(distance)
            pair = np.rint((sides[0].astype(float)+sides[1])*.5).astype(np.uint8)
            a = cv2.cvtColor(sides[0][None], cv2.COLOR_RGB2LAB)[0].astype(float)
            b = cv2.cvtColor(sides[1][None], cv2.COLOR_RGB2LAB)[0].astype(float)
            a[:, 0] *= .4
            b[:, 0] *= .4
            delta = np.linalg.norm(a-b, axis=1)
            candidates.append(pair)
            fits.append((lengths[0] > 0) & (lengths[1] > 0) & (delta <= 6))
            costs.append(delta+.3*(lengths[0]+lengths[1]))
        candidates, fits, costs = np.stack(candidates), np.stack(fits), np.stack(costs)
        lab = cv2.cvtColor(candidates.reshape(1, -1, 3), cv2.COLOR_RGB2LAB).reshape(candidates.shape).astype(float)
        lab[:, :, 0] *= .4
        agree = np.linalg.norm(lab[:, None]-lab[None, :], axis=3) < 4
        support = (agree & fits[:, None] & fits[None, :]).sum(axis=1)
        choice = np.where(fits, support*100-costs, -1e9).argmax(axis=0)
        indices = np.arange(count)
        incompatible = np.where(~agree[choice, :, indices].T, support, 0).max(axis=0)
        take = (support[choice, indices] >= 2) & (support[choice, indices] > incompatible)
        result[y[take], x[take]] = candidates[choice[take], indices[take]]
        accepted += int(take.sum())
    return cv2.medianBlur(result, 5), accepted


def scan_grain(rgb, valid):
    """Estimate chromatic grain on bright, slowly varying paper, in Lab units.

    Median residuals reject isolated glyphs and genuine colour transitions.
    NoData, dark ink and strong broad gradients do not vote for denoising.
    A substantial noisy part must not disappear behind a clean sheet median.
    """
    if np.count_nonzero(valid) < 256:
        return 0.
    indices = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = rgb[tuple(indices)]
    lab = cv2.cvtColor(filled, cv2.COLOR_RGB2LAB).astype(np.float32)
    median = cv2.cvtColor(cv2.medianBlur(filled, 3), cv2.COLOR_RGB2LAB).astype(np.float32)
    broad = cv2.GaussianBlur(lab, (0, 0), 3)
    gx = cv2.Sobel(broad, cv2.CV_32F, 1, 0)/8
    gy = cv2.Sobel(broad, cv2.CV_32F, 0, 1)/8
    paper = (valid & (lab[:, :, 0] > 180) & (np.linalg.norm(gx, axis=2) < 3)
             & (np.linalg.norm(gy, axis=2) < 3))
    if np.count_nonzero(paper) < 128:
        return 0.
    residual = np.linalg.norm(lab[:, :, 1:]-median[:, :, 1:], axis=2)
    grain = float(np.median(residual[paper]))
    if grain > 2.5:
        return grain
    noisy = []
    noisy_area = 0
    # Reuse the full-image cues: no extra reconstruction or source-sized arrays.
    # Multiple independently supported windows and their valid area reject a
    # small damaged corner, a label or a sparse sliver along a NoData boundary.
    for y in range(0, valid.shape[0], 192):
        for x in range(0, valid.shape[1], 192):
            roi = np.s_[y:y+192, x:x+192]
            area = int(np.count_nonzero(valid[roi]))
            support = paper[roi]
            if area < 1024 or np.count_nonzero(support) < max(128, .1*area):
                continue
            local = float(np.median(residual[roi][support]))
            if local > 2.5:
                noisy.append(local)
                noisy_area += area
    if len(noisy) >= 2 and noisy_area >= .1*np.count_nonzero(valid):
        grain = max(grain, float(np.median(noisy)))
    return grain


def solid_fill_anchors(rgb, valid):
    """Coherent dark colour interiors, independent of local paper brightness."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    light = lab[:, :, 0]
    # Solid chromatic ribbons and broad dark fills must remain colour anchors.
    # Require a stable interior; the untouched strokes remain in contact cues.
    stable = cv2.medianBlur(lab, 3)
    chroma = np.linalg.norm(stable[:, :, 1:].astype(np.float32)-128, axis=2)
    chromatic = ((chroma > 55) & (stable[:, :, 0] < 180) & valid).astype(np.uint8)
    dark = ((light < 120) & valid).astype(np.uint8)
    protected = (cv2.morphologyEx(chromatic, cv2.MORPH_OPEN,
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0)
    components, _ = ndimage.label(dark)
    sizes = np.bincount(components.ravel())
    for ident, bounds in enumerate(ndimage.find_objects(components), 1):
        if bounds is None or sizes[ident] < 256:
            continue
        rows, cols = bounds
        height, width = rows.stop-rows.start, cols.stop-cols.start
        if min(height, width) >= 16 and sizes[ident] >= .8*height*width:
            local = components[bounds] == ident
            protected[bounds] |= local
    return protected


def reconstruct_grainy_scan(rgb, valid, check=None):
    """Recover paper under thick print before reducing halftone colour chatter.

    This appearance image is separate from the untouched raster used for ink,
    contacts and texture. Work is bounded by the overview's image size.
    """
    filled, ink, fallback = reconstruct_colors(rgb, valid)
    lab = cv2.cvtColor(filled, cv2.COLOR_RGB2LAB)
    light = lab[:, :, 0]
    background = cv2.morphologyEx(light, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    broad_ink = (light.astype(np.int16) < background.astype(np.int16)-28) & (light < 190)
    ink = ndimage.binary_dilation(ink | broad_ink) & valid
    protected = solid_fill_anchors(filled, valid)
    ink &= ~protected
    anchors = valid & ~ink
    if not anchors.any():
        return filled, ink, fallback
    if check is not None:
        check()
    if valid.all():
        nearest = ndimage.distance_transform_edt(ink, return_distances=False, return_indices=True)
        reconstructed = filled[tuple(nearest)]
    else:
        # A masked geodesic flood cannot borrow paper through a NoData hole or
        # from another disconnected island. Unseeded islands retain their RGB.
        from skimage.segmentation import watershed
        markers = np.zeros(valid.shape, np.int32)
        flat = np.flatnonzero(anchors)
        markers.ravel()[flat] = flat+1
        owners = watershed(np.zeros(valid.shape, np.uint8), markers, mask=valid, connectivity=1)
        reconstructed = filled.copy()
        take = ink & (owners > 0)
        reconstructed[take] = filled.reshape(-1, 3)[owners[take]-1]
    if check is not None:
        check()
    reconstructed = cv2.fastNlMeansDenoisingColored(reconstructed, None, 7, 12, 7, 21)
    if check is not None:
        check()
    # Seek local colour modes rather than average across a colour edge. This
    # removes coherent scan mottling while strong, narrow fills remain distinct.
    reconstructed = cv2.pyrMeanShiftFiltering(reconstructed, 10, 25, maxLevel=1)
    reconstructed[~valid] = filled[~valid]
    if check is not None:
        check()
    return filled, ink, reconstructed
