"""Detect coherent patches of parallel print and fit them to the existing partition."""
import cv2
import numpy as np
from scipy import ndimage
from scipy.fft import rfft2
from skimage.measure import label as connected_labels
from skimage.segmentation import watershed


WINDOW = 24
STRIDE = 3


def spectral_grid(rgb, valid):
    """Estimate the direction, frequency and confidence of local periodic ink."""
    gray = (cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) if rgb.ndim == 3 else rgb).astype(np.float32)
    if not valid.all():
        idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        gray = gray[tuple(idx)]
    signal = gray - cv2.GaussianBlur(gray, (0, 0), 2.)
    window = np.outer(np.hanning(WINDOW), np.hanning(WINDOW)).astype(np.float32)

    def windows(image):
        pad = ((WINDOW//2, WINDOW//2-1),)*2
        padded = np.pad(image, pad, mode='edge')
        views = np.lib.stride_tricks.sliding_window_view(padded, (WINDOW, WINDOW))
        return views[::STRIDE, ::STRIDE]

    tiles, support = windows(signal), windows(valid)
    score = np.zeros(tiles.shape[:2], np.float32)
    direction, frequency = score.copy(), score.copy()
    fy, fx = np.fft.fftfreq(WINDOW)*WINDOW, np.fft.rfftfreq(WINDOW)*WINDOW
    yy, xx = np.meshgrid(fy, fx, indexing='ij')
    k = np.hypot(xx, yy)
    allowed = (k >= 3) & (k <= 9)
    weights = np.where((xx == 0) | (xx == WINDOW/2), 1., 2.).astype(np.float32)
    for row in range(0, len(score), 4):
        sl = slice(row, row+4)
        patch = tiles[sl]
        power = abs(rfft2(patch*window, axes=(-2,-1)))**2
        total = (power*weights).sum(axis=(-1,-2))
        flat = np.where(allowed, power, 0).reshape(*patch.shape[:2], -1)
        iy, ix = np.unravel_index(flat.argmax(axis=-1), k.shape)
        peak = flat.max(axis=-1)
        amplitude = 2*np.sqrt(peak)/window.sum()
        score[sl] = np.where((amplitude > 5) & (support[sl].mean(axis=(-2,-1)) > .4),
                             2*peak/np.maximum(total, 1.), 0.)
        direction[sl] = np.arctan2(fy[iy], fx[ix]) % np.pi
        frequency[sl] = k[iy, ix]
    return score, direction, frequency


def thin_band_signal(gray, across, along, period, angle, border_light=None):
    """Look for the established cadence along a thin row between printed borders."""
    window = np.exp(-.5*((across/period)**2 + (along/(.14*period))**2))
    window = (window/window.sum()).astype(np.float32)
    carrier = np.exp(2j*np.pi*across/period)
    kernel = window*(carrier-np.sum(window*carrier))
    real = cv2.filter2D(gray,-1,kernel.real.astype(np.float32))
    imag = cv2.filter2D(gray,-1,kernel.imag.astype(np.float32))
    amplitude = 4*(real*real + imag*imag)
    mean = cv2.filter2D(gray,-1,window)
    variance = cv2.filter2D(gray*gray,-1,window)-mean*mean
    # A single edge also has a Fourier response. Require a darker border on
    # each side, searching along the direction of the printed strokes.
    flank_radius = max(2,round((1.4 if border_light is None else 1.6)*period))
    diameter = 2*flank_radius+1
    forward = np.zeros((diameter,diameter),np.uint8)
    for distance in range(2,flank_radius+1):
        x = flank_radius-round(distance*np.sin(angle))
        y = flank_radius+round(distance*np.cos(angle))
        forward[y,x] = 1
    if border_light is not None:
        gray = border_light
        mean = cv2.filter2D(gray,-1,window)
    left = cv2.erode(gray,forward)
    right = cv2.erode(gray,np.flip(forward).copy())
    paired = (mean-left > 12.) & (mean-right > 12.)
    minimum_share = .40 if border_light is None else .30
    return paired & (amplitude > 36) & (amplitude > minimum_share*np.maximum(1.,variance))


def extend_texture_fields(gray, cores, valid, angle, cycles, border_light=None):
    """Follow a known periodic signal with a smaller window at narrow contacts."""
    period = WINDOW / cycles
    radius = int(np.ceil(2.3*period))
    y, x = np.mgrid[-radius:radius+1, -radius:radius+1]
    across = x*np.cos(angle) + y*np.sin(angle)
    along = y*np.cos(angle) - x*np.sin(angle)
    window = np.exp(-.5*((across/(.85*period))**2 + (along/(.4*period))**2))
    window /= window.sum()
    phase = np.exp(2j*np.pi*across/period)
    kernel = window*(phase-np.sum(window*phase))
    signal = gray-cv2.GaussianBlur(gray,(0,0),period/2)
    real = cv2.filter2D(signal,-1,kernel.real.astype(np.float32))
    imag = cv2.filter2D(signal,-1,kernel.imag.astype(np.float32))
    energy = cv2.filter2D(signal*signal,-1,window.astype(np.float32))
    amplitude_squared = 4*(real*real+imag*imag)
    evidence = (amplitude_squared > 36) & (amplitude_squared > .55*energy)
    # Allow a short interruption from a crossing printed mark only within the
    # valid raster. The subsequent colour and contact constraints still apply.
    support = (evidence | thin_band_signal(gray, across, along, period, angle, border_light) | cores) & valid
    gap = int(np.ceil(1.2*period))
    closed = ndimage.binary_closing(support,iterations=gap)
    fields, count = ndimage.label((support | closed) & valid)
    anchored = np.bincount(fields[cores],minlength=count+1) > 0
    anchored[0] = False
    return np.where(anchored[fields], fields, 0)


def hatch_patches(rgb, valid, spectrum=None, *, border_light=None):
    score, direction, frequency = spectrum if spectrum is not None else spectral_grid(rgb, valid)
    bins = 12
    angle = np.rint(direction*bins/np.pi).astype(int) % bins
    cycles = np.rint(frequency).astype(int)
    valid_sample = valid[::STRIDE, ::STRIDE]
    best = np.zeros(score.shape, np.float32)
    patches = np.zeros(score.shape, np.int32)
    confident = score > .205
    next_id = 1
    signatures = []
    for di, fi in np.unique(np.column_stack((angle[confident], cycles[confident])), axis=0):
        angular = np.minimum((angle-di) % bins, (di-angle) % bins)
        weak = (angular <= 1) & (np.abs(frequency-fi) <= .85) & (score > .055) & valid_sample
        support = ndimage.binary_closing(weak, structure=np.ones((3,3))) | weak
        labels, count = ndimage.label(support & valid_sample)
        areas = np.bincount(labels.ravel(), minlength=count+1)
        strong = weak & confident & (angular == 0) & (cycles == fi)
        hits = np.bincount(labels[strong], minlength=count+1)
        # Require consistent orientation and spacing in the supporting windows.
        keep = (hits >= 8) & (hits >= areas*.30) & (areas >= 24)
        keep[0] = False
        confidence = np.where(keep, hits/np.maximum(1,areas), 0.)[labels]
        update = confidence > best
        ids = np.zeros(count+1,np.int32)
        ids[keep] = np.arange(next_id, next_id+keep.sum())
        next_id += int(keep.sum())
        signatures.extend([(di,fi)]*int(keep.sum()))
        patches[update] = ids[labels[update]]
        best[update] = confidence[update]
    # The sample at grid column j represents the window centred at STRIDE*j.
    y = np.minimum((np.arange(valid.shape[0])+STRIDE//2)//STRIDE, patches.shape[0]-1)
    x = np.minimum((np.arange(valid.shape[1])+STRIDE//2)//STRIDE, patches.shape[1]-1)
    cores = np.where(valid, patches[np.ix_(y,x)], 0)
    result = cores.copy()
    gray = (cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) if rgb.ndim == 3 else rgb).astype(np.float32)
    for angle_bin, cycles in sorted(set(signatures)):
        members = [i for i, signature in enumerate(signatures,1) if signature == (angle_bin, cycles)]
        seed = np.isin(cores,members)
        if not seed.any():
            continue
        fields = extend_texture_fields(gray,seed,valid,angle_bin*np.pi/bins,cycles,border_light)
        new = (fields > 0) & (seed | (result == 0))
        result[new] = next_id + fields[new]
        next_id += int(fields.max())+1
    return result


def add_colour_hatching(rgb, valid, spectrum, patches):
    """Find additional stripes where light and chroma agree on spacing and direction."""
    light_score, light_angle, light_frequency = spectrum
    if not np.any(light_score > .065):
        return patches
    light = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    for channel in (1, 2):
        chroma = lab[:, :, channel]
        if np.ptp(chroma[valid]) < 4:
            continue
        signal = np.clip(128 + 4 * (chroma.astype(np.int16) - 128), 0, 255).astype(np.uint8)
        score, angle, frequency = spectral_grid(signal, valid)
        agreement = ((light_score > .065) & (abs(np.cos(angle - light_angle)) > .95)
                     & (abs(frequency - light_frequency) <= 1))
        if not np.any(agreement & (score > .205)):
            continue
        extra = hatch_patches(signal, valid, (np.where(agreement, score, 0), angle, frequency),
                              border_light=light)
        # Only add whole fields that do not compete with an established hatch.
        keep = np.bincount(extra.ravel()) > 0
        keep[0] = False
        keep[extra[patches > 0]] = False
        if keep.any():
            lookup = np.zeros(len(keep), dtype=np.int32)
            lookup[keep] = int(patches.max()) + np.arange(1, keep.sum() + 1)
            additions = lookup[extra]
            patches = np.where(additions > 0, additions, patches)
    return patches


def bridge_ambiguous_colour(domain, patch, prepared, roi, unit, barrier):
    """Join nearby seeded components through a short, colour-ambiguous interruption."""
    labels, count = ndimage.label(domain & patch)
    sizes = np.bincount(labels.ravel(), minlength=count+1)
    sizes[0] = 0
    if np.count_nonzero(sizes >= 48) < 2:
        return domain
    colors = prepared.lab[roi]
    best = np.linalg.norm(colors-prepared.centers[prepared.assignment[roi].astype(int)-1],axis=2)
    target = np.linalg.norm(colors-prepared.centers[unit-1],axis=2)
    plausible = (target <= best+4.) & ~barrier & patch & prepared.raster.valid[roi]
    # Add only ambiguous pixels that actually connect two sizeable interiors.
    added = ndimage.binary_closing(domain,iterations=8) & ~domain & plausible
    gaps, total = ndimage.label(added)
    for ident, box in enumerate(ndimage.find_objects(gaps),1):
        if box is None:
            continue
        around = tuple(slice(max(0,s.start-1),min(n,s.stop+1)) for s,n in zip(box,domain.shape))
        gap = gaps[around] == ident
        neighbours = np.unique(labels[around][ndimage.binary_dilation(gap)])
        if np.count_nonzero(sizes[neighbours] >= 48) >= 2:
            domain[around] |= gap
    return domain


def narrow_fragments(objects, classes, patches):
    """Select elongated regions where periodic ink covers much of the fragment."""
    bounds = ndimage.find_objects(objects)
    counts = np.bincount(objects.ravel(), minlength=len(classes))
    support = np.bincount(objects[patches > 0], minlength=len(classes))
    fragments = {}
    for ident in np.flatnonzero((counts >= 24) & (support >= .55*counts)):
        if not ident or not classes[ident]:
            continue
        box = bounds[ident-1]
        height, width = (s.stop-s.start for s in box)
        length = max(height, width)
        if counts[ident]/length > 9 or length**2 < 7*counts[ident]:
            continue
        fragments[ident] = box
    return fragments


def absorb_cut_remnants(original, fitted, classes, first_hatch, boundary):
    """Resolve small chips left when a fitted texture cuts through an older region."""
    changed = original != fitted
    if not changed.any():
        return fitted
    removed = np.bincount(original[changed], minlength=first_hatch)
    components = connected_labels(fitted, connectivity=1, background=0)
    areas = np.bincount(components.ravel())
    boxes = ndimage.find_objects(components)
    output = fitted.copy()
    maximum = 32  # working pixels
    for part in np.flatnonzero((areas > 0) & (areas <= maximum)):
        if part == 0:
            continue
        box = boxes[part-1]
        roi = tuple(slice(max(0,axis.start-1),min(n,axis.stop+1))
                    for axis,n in zip(box,fitted.shape))
        local = components[roi]
        y, x = np.nonzero(local == part)
        owner = fitted[roi][y[0],x[0]]
        if owner >= first_hatch or not classes[owner] or removed[owner] < 4*areas[part]:
            continue
        length = max(axis.stop-axis.start for axis in box)
        if length >= 12 and length**2 > 8*areas[part]:
            continue  # keep a slender lens even when it is only a few pixels wide
        # Compare face neighbours in the unchanged partition. Requiring a
        # larger recipient prevents chains of tiny chips shifting a contact.
        labels = np.pad(fitted[roi],1)
        pieces = np.pad(local,1)
        evidence = np.pad(boundary[roi],1)
        y, x = y+1, x+1
        faces = ((-1,0),(1,0),(0,-1),(0,1))
        neighbours = np.stack([labels[y+dy,x+dx] for dy,dx in faces])
        if not np.any(neighbours >= first_hatch):
            continue
        adjacent = np.stack([pieces[y+dy,x+dx] for dy,dx in faces])
        strength = np.stack([.5*(evidence[y,x]+evidence[y+dy,x+dx]) for dy,dx in faces])
        eligible = (areas[adjacent] > maximum) & (classes[neighbours] == classes[owner])
        ids, inverse, lengths = np.unique(neighbours[eligible],return_inverse=True,return_counts=True)
        if not len(ids):
            continue
        mean = np.bincount(inverse,weights=strength[eligible])/lengths
        support = lengths*(1.-np.minimum(mean,1.))
        support[(mean >= .80) | (lengths < 2)] = 0
        if not support.any():
            continue
        # A fragment lies outside the fitted contour. Keep that contour when
        # an existing exterior neighbour offers a similarly supported join.
        exterior = (ids < first_hatch) & (support >= .5*support.max())
        if exterior.any():
            support[~exterior] = 0
        output[roi][y-1,x-1] = ids[support.argmax()]
    return output


def extend_fitted_tips(fitted, target, classes, first_fitted, patch, lab, centers, assignment, barrier):
    """Fit up to two adjacent, tapering fragments at either end of a printed band."""
    core = fitted == target
    if core.sum() < 48 or ndimage.label(core)[1] != 1:
        return
    # Work inside the field's local window. A clipped fragment has unknown outline.
    regions = connected_labels(fitted, connectivity=1, background=0)
    clipped = np.unique(np.r_[regions[0], regions[-1], regions[:, 0], regions[:, -1]])
    counts = np.bincount(regions.ravel())
    areas = np.bincount(regions[patch], minlength=len(counts))
    unit = classes[target]
    for _ in range(2):
        coords = np.column_stack(np.nonzero(core))
        values, axes = np.linalg.eigh(np.cov(coords.T))
        if values[1] < 4 * max(1., values[0]):
            break
        along, across = coords @ axes[:, 1], coords @ axes[:, 0]
        width = np.percentile(across, 90) - np.percentile(across, 10)
        step = max(4., width / 2)
        rim = ndimage.binary_dilation(core) & ~core
        marked_contact = barrier | ndimage.binary_dilation(core & barrier)
        winners = {}
        for ident in np.setdiff1d(np.unique(regions[rim]), clipped):
            if not ident or counts[ident] < 8 or counts[ident] > .5 * len(coords):
                continue
            if areas[ident] < .35 * counts[ident]:
                continue
            mask = regions == ident
            owner = fitted[mask][0]
            # Already fitted fields have their own geometry.
            if owner >= first_fitted or not classes[owner]:
                continue
            contact = rim & mask
            if contact.sum() < 3 or np.mean(marked_contact[contact]) > .5:
                continue
            if classes[owner] != unit:
                colors = lab[mask]
                closest = centers[assignment[mask].astype(int) - 1]
                penalty = np.linalg.norm(colors - centers[unit-1], axis=1) - np.linalg.norm(colors - closest, axis=1)
                if np.median(penalty) > 3:
                    continue
            candidate = np.column_stack(np.nonzero(mask))
            forward = candidate @ axes[:, 1]
            sign = 1 if forward.mean() > along.mean() else -1
            end = np.max(sign * along)
            distance = sign * forward - end
            if np.median(distance) <= 0 or distance.max() > 4 * step:
                continue
            # Estimate both sides in consecutive cross sections at the end.
            near = end - sign * along
            last = across[near < step]
            prior = across[(near >= step) & (near < 2 * step)]
            if min(len(last), len(prior)) < 5:
                continue
            sides = np.percentile(last, [10, 90])
            previous = np.percentile(prior, [10, 90])
            slope = np.clip((sides - previous) / step, -.5, .5)
            cross = candidate @ axes[:, 0]
            offset = np.clip(distance + step / 2, 0, 2 * step)
            tolerance = max(2., .5 * step)
            aligned = ((cross >= sides[0] + slope[0] * offset - tolerance)
                       & (cross <= sides[1] + slope[1] * offset + tolerance))
            if aligned.mean() >= .70:
                winners.setdefault(sign, []).append(ident)
        # A fork is ambiguous. Keep the existing boundary at that end.
        tips = [ids[0] for ids in winners.values() if len(ids) == 1]
        if not tips:
            break
        added = np.isin(regions, tips)
        core |= added
        fitted[added] = target


def fit_hatching(objects, classes, patches, prepared, *, automatic_palette=False):
    """Use texture interiors as seeds. Constrain the fit to one selected colour."""
    if not patches.any():
        return objects, classes
    result = objects.copy()
    new_classes = list(classes)
    tip_fields = []
    valid = prepared.raster.valid
    sizes_before = np.bincount(objects.ravel())
    gray = cv2.cvtColor(prepared.raster.rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    dark = cv2.GaussianBlur(gray,(0,0),4) - gray
    # Dense stripes contribute to local RMS. A much darker crossing stroke may
    # be a genuine contact and must remain available to split a texture field.
    rms = np.sqrt(cv2.GaussianBlur(dark*dark,(0,0),5))
    barrier = dark > np.maximum(44., 2.1*rms)
    # Extend long dark contacts through the uncertain rim of the spectral
    # window. Use only directions supported by a continuous stroke.
    ink = barrier.astype(np.uint8)
    for theta in np.arange(8)*np.pi/8:
        dx, dy = np.rint(8*np.array([np.cos(theta),np.sin(theta)])).astype(int)
        kernel = np.zeros((17,17),np.uint8)
        cv2.line(kernel,(8-dx,8-dy),(8+dx,8+dy),1)
        long_stroke = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
        barrier |= cv2.dilate(long_stroke, kernel).astype(bool)
    fragments = narrow_fragments(objects, classes, patches)
    for ident, bounds in enumerate(ndimage.find_objects(patches),1):
        if bounds is None:
            continue
        roi = tuple(slice(max(0,s.start-12), min(n,s.stop+12)) for s,n in zip(bounds,valid.shape))
        patch = patches[roi] == ident
        class_image = classes[objects[roi]]
        votes = np.bincount(class_image[patch], minlength=len(prepared.palette)+1)
        unit = int(votes.argmax())
        if not unit or votes[unit] < .65*votes.sum():
            continue
        domain = valid[roi] & (class_image == unit)
        members = np.bincount(objects[roi][patch & domain], minlength=len(sizes_before))
        contained = (members >= 18) & (members >= .40*sizes_before)
        if contained.sum() < 2:
            continue
        # Retain the drawn outline of an existing tapered fragment when a
        # substantial part of it already belongs to this texture field.
        attached = [i for i in fragments if classes[i] == unit and members[i] >= .20*sizes_before[i]]
        if attached:
            boxes = [bounds] + [fragments[i] for i in attached]
            roi = tuple(slice(max(0,min(b[axis].start for b in boxes)-12),
                              min(n,max(b[axis].stop for b in boxes)+12))
                        for axis,n in enumerate(valid.shape))
            patch = patches[roi] == ident
            domain = valid[roi] & (classes[objects[roi]] == unit)
        arms = np.isin(objects[roi], attached) if attached else None
        if automatic_palette:
            domain = bridge_ambiguous_colour(domain, patch, prepared, roi, unit, barrier[roi])
        inner = ndimage.binary_erosion(patch, iterations=2) & domain
        if not inner.any():
            continue
        outer = ndimage.binary_dilation(patch, iterations=7) & domain
        if attached:
            # Grow the marker only inside connected arms. A printed crossing
            # contact still separates the final components below.
            extensions = ndimage.binary_propagation(inner, mask=(inner | arms) & ~barrier[roi])
            inner |= extensions
            # A tapered continuation gives a well-localized inner seed. Keep
            # the outer search within the nearby contact while fitting it.
            outer = (ndimage.binary_dilation(patch, iterations=4) | arms) & domain
        markers = np.where(inner, 1, np.where(~outer & domain, 2, 0))
        boundary = prepared.boundary[roi]
        if attached:
            # Ink darker than the local hatch contrast supports the centre of
            # a printed contact even when the two paper colours are similar.
            excess = np.maximum(0.,dark[roi]-rms[roi])
            boundary = np.maximum(boundary,np.minimum(excess/65.,1.))
        fill = watershed(boundary, markers, mask=domain) == 1
        # Use strong crossing contacts only when they actually divide the field.
        # Short strokes and map lettering may end in its interior.
        interior, count = ndimage.label(fill & ~barrier[roi])
        areas = np.bincount(interior.ravel(), minlength=count+1)
        anchored = np.bincount(interior[inner], minlength=count+1)
        keep = (areas >= 48) & (anchored >= 18)
        keep[0] = False
        if not keep.any():
            continue
        seeds = np.where(keep[interior], interior, 0)
        pieces = watershed(prepared.boundary[roi], seeds, mask=fill)
        for piece in np.flatnonzero(keep):
            pixels = (pieces == piece) & (result[roi] < len(classes))
            if not pixels.any():
                continue
            result[roi][pixels] = len(new_classes)
            new_classes.append(unit)
            if automatic_palette:
                tip_fields.append((len(new_classes)-1, ident, roi))
    # Reserve all established interiors before testing their possible continuations.
    for target, ident, roi in tip_fields:
        extend_fitted_tips(result[roi], target, new_classes, len(classes), patches[roi] == ident,
                           prepared.lab[roi], prepared.centers, prepared.assignment[roi], barrier[roi])
    new_classes = np.asarray(new_classes, dtype=classes.dtype)
    result = absorb_cut_remnants(objects,result,new_classes,len(classes),prepared.boundary)
    return result, new_classes
