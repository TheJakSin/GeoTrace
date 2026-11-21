"""Joint appearance/boundary segmentation. No geological class inference.

Palette estimation uses deterministic, bounded samples. Raw ink and texture are
retained separately from reconstructed colors. Region seeds compete over a
multiscale boundary surface; a shared partition is vectorized once.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import math
import time

import cv2
import numpy as np
from scipy import ndimage
from rasterio.features import shapes, rasterize, sieve
from shapely.geometry import shape
from skimage.measure import label as connected_labels
from skimage.segmentation import watershed

from .core import Raster, Result
from .preprocessing import reconstruct_colors, consensus_colors, scan_grain, reconstruct_grainy_scan, solid_fill_anchors
from .hatching import add_colour_hatching, hatch_patches, fit_hatching, spectral_grid
from .contacts import print_codes, reconcile_contacts
from .interior_contacts import split_interior_contacts


class Cancelled(Exception):
    pass


def checkpoint(folder, stage, progress):
    if folder:
        folder = Path(folder)
        if (folder/'quality-cancel').exists():
            raise Cancelled('Расчёт остановлен. Прежние полигоны сохранены.')
        temp = folder/'quality-progress.tmp'
        for attempt in range(5):
            if (folder/'quality-cancel').exists():
                raise Cancelled('Расчёт остановлен. Прежние полигоны сохранены.')
            try:
                temp.write_text(json.dumps(dict(stage=stage, progress=progress)), encoding='utf-8')
                temp.replace(folder/'quality-progress.json')
                break
            except PermissionError:
                # Windows can briefly deny replacement while the UI reads the
                # previous frame. Progress is advisory; keep computing after
                # bounded retries rather than discarding a valid calculation.
                if attempt < 4:
                    time.sleep(.01)



@dataclass
class Prepared:
    raster: Raster
    lab: np.ndarray
    boundary: np.ndarray
    centers: np.ndarray
    assignment: np.ndarray
    margin: np.ndarray
    palette: list
    source_note: str = ''
    scale: float = 1.
    line_evidence: np.ndarray | None = None
    shades: np.ndarray | None = None
    overview: "Prepared | None" = None
    hatching: np.ndarray | None = None
    sampled: bool = False
    print_texture: np.ndarray | None = None
    boundary_lab: np.ndarray | None = None
    grain: float = 0.


def gradient(lab, sigma):
    blur = cv2.GaussianBlur(lab, (0, 0), sigma)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3) / 8
    return np.sqrt((gx*gx + gy*gy).sum(axis=2))


def assign(lab, centers):
    labels = np.zeros(lab.shape[:2], np.uint8)
    margin = np.zeros(labels.shape, np.float32)
    for y in range(0, len(lab), 64):
        distances = ((lab[y:y+64, :, None] - centers[None, None])**2).sum(axis=3)
        labels[y:y+64] = distances.argmin(axis=2) + 1
        if len(centers) > 1:
            best = np.partition(distances, 1, axis=2)[:, :, :2]
            margin[y:y+64] = np.sqrt(best[:, :, 1]) - np.sqrt(best[:, :, 0])
        else:
            margin[y:y+64] = 100
    return labels, margin


def palette_centers(lab, valid, folder=None):
    samples = lab[valid]
    rng = np.random.default_rng(42)
    if len(samples) > 20000:
        samples = samples[rng.choice(len(samples), 20000, replace=False)]
    unique = len(np.unique(samples.round(), axis=0))
    best, score = None, np.inf
    # Penalize extra groups in Lab distance units, rather than displaying all
    # printer/shadow colors. This is a heuristic, not a geological class count.
    for k in range(1, min(12, unique) + 1):
        checkpoint(folder, 'Подбираю устойчивые группы заливок…', 20 + k)
        cv2.setRNGSeed(42)
        loss, _, centers = cv2.kmeans(samples, k, None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, .1), 2, cv2.KMEANS_PP_CENTERS)
        objective = loss/len(samples) + 12*k
        if objective < score:
            best, score = centers, objective
    # Keep the established result for maps with a compact palette. A filled
    # rescue limit is evidence to explore further, even when global distortion
    # prefers few clusters: rare printed fills have little weight in that loss.
    base = best.copy()
    best = _coherent_palette(lab, valid, centers, base, 12, 4)
    if len(best) == 12 and unique > 12:
        for k in range(13, min(24, unique) + 1):
            checkpoint(folder, 'Проверяю дополнительные различимые заливки…', 32)
            cv2.setRNGSeed(42)
            loss, _, centers = cv2.kmeans(samples, k, None,
                (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, .1),
                2, cv2.KMEANS_PP_CENTERS)
            objective = loss/len(samples) + 12*k
            if objective < score:
                base, score = centers, objective
        # Rebuild against the finer proposals rather than retaining the
        # coarser rescue centres too: those can duplicate mixed print colours.
        best = _coherent_palette(lab, valid, centers, base, 24, 4)
    return best[np.argsort(best[:, 0])]


def _coherent_palette(lab, valid, centers, best, limit, minimum):
    # Global distortion underweights rare colors. Retain a distinct candidate
    # when it occupies at least one coherent patch, even with a tiny map share.
    fine_labels, _ = assign(lab, centers)
    for i, center in enumerate(centers, 1):
        if len(best)>=limit: break
        if (np.linalg.norm(best-center, axis=1).min() < 10
                or np.linalg.norm(best[:,1:]-center[1:],axis=1).min()<10):
            continue
        components, _ = ndimage.label(ndimage.binary_erosion((fine_labels==i)&valid))
        counts = np.bincount(components.ravel()); counts[0] = 0
        if counts.max(initial=0) >= minimum:
            best = np.vstack((best, center))
    # Histogram rescue catches tiny chromatic fills that k-means may never
    # initialize. Ignore brightness-only shadows and require a coherent patch.
    bins=(lab[:,:,1:].astype(np.int16)//8)
    codes=bins[:,:,0]*32+bins[:,:,1]
    ids,counts=np.unique(codes[valid],return_counts=True)
    for ident,count in sorted(zip(ids,counts),key=lambda x:-x[1]):
        if count<12 or len(best)>=limit: continue
        mask=(codes==ident)&valid
        center=np.median(lab[mask],axis=0)
        if np.linalg.norm(best[:,1:]-center[1:],axis=1).min()<14: continue
        connected,_=ndimage.label(ndimage.binary_erosion(mask))
        sizes=np.bincount(connected.ravel());sizes[0]=0
        if sizes.max(initial=0)>=minimum: best=np.vstack((best,center))
    return best


def refine_fill_shades(lab, assignment, shades):
    """Resolve interior tones only when they form stable patches of one fill."""
    refined = shades.copy()
    for group in np.unique(assignment):
        if group == 0:
            continue
        mask = assignment == group
        pixels = lab[mask]
        samples = pixels[::max(1,len(pixels)//12000)]
        if len(samples) < 80 or np.max(samples.std(axis=0)) < 4:
            continue
        cv2.setRNGSeed(42)
        _, _, centers = cv2.kmeans(samples, 3, None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,30,.1), 2, cv2.KMEANS_PP_CENTERS)
        separation = np.linalg.norm(centers[:,None]-centers,axis=2)
        if separation[np.triu_indices(3,1)].min() < 4:
            continue
        # Limit temporary distances on large maps. Only classify pixels of this fill.
        labels = np.empty(len(pixels),np.uint8)
        for start in range(0,len(pixels),32768):
            block = pixels[start:start+32768]
            distances = np.square(block[:,None]-centers).sum(axis=2)
            labels[start:start+len(block)] = distances.argmin(axis=1)+1
        local = np.zeros(mask.shape,np.uint8)
        local[mask] = labels
        # A sieve may leave islands intact when all neighbouring islands are
        # small too. Each tone needs at least one independently connected patch.
        regions = connected_labels(local,background=0,connectivity=1)
        areas = np.bincount(regions.ravel())
        supported = mask & (areas[regions]>=80)
        if np.unique(local[supported]).size < 3:
            continue
        patches = sieve(local,size=80,mask=mask,connectivity=4)
        kept = patches[mask]
        counts = np.bincount(labels,minlength=4)[1:]
        stable = np.bincount(labels[kept==labels],minlength=4)[1:]
        if np.all(counts>0) and np.all(stable>=.80*counts):
            refined[mask] = kept
    return refined


def prepare(raster, folder=None, appearance=None):
    cv2.setNumThreads(2)
    checkpoint(folder, 'Разделяю заливку и печатные штрихи…', 5 if appearance is None else 50)
    scale = 1. if appearance is None else raster.rgb.shape[1]/appearance.raster.rgb.shape[1]
    if appearance is None:
        grain = scan_grain(raster.rgb, raster.valid)
        if grain > 2.5:
            check = lambda: checkpoint(folder, 'Восстанавливаю заливку зернистого скана…', 12)
            filled, ink_mask, reconstructed = reconstruct_grainy_scan(raster.rgb, raster.valid, check)
        else:
            filled, ink_mask, reconstructed = reconstruct_colors(raster.rgb, raster.valid)
        clean = cv2.medianBlur(reconstructed, 5)
        lab = cv2.cvtColor(clean, cv2.COLOR_RGB2LAB).astype(np.float32)
        lab[:, :, 0] *= .4
        raw_light = cv2.cvtColor(raster.rgb,cv2.COLOR_RGB2LAB)[:,:,0].astype(np.float32)
        local_light = ndimage.percentile_filter(raw_light,70,size=9)
        reliable = raster.valid & (raw_light>=local_light-8) & (gradient(lab,1)<4)
        if grain > 2.5:
            # Recovered appearance already separates paper from print. A dark,
            # narrow chromatic unit is valid palette evidence too; a local
            # brightness test alone would mistake it for an ink stroke.
            reliable = (reliable & ~ink_mask) | solid_fill_anchors(filled, raster.valid)
        if reliable.sum()<16: reliable=raster.valid
        centers = palette_centers(lab, reliable, folder)
    else:
        grain = appearance.grain
        # Global color evidence is shared across scales. Native ink refines the
        # contours without rediscovering a different palette in every tile.
        boundary_lab = appearance.boundary_lab if appearance.boundary_lab is not None else appearance.lab
        lab=cv2.resize(boundary_lab,(raster.rgb.shape[1],raster.rgb.shape[0]),interpolation=cv2.INTER_LINEAR)
        centers=appearance.centers.copy()
    # Internal tonal partitions retain weak transitions inside a display swatch.
    # These are region proposals; the operator only sees stable palette colors.
    if appearance is None:
        tonal_samples = lab[raster.valid]
        if len(tonal_samples)>50000:
            pick=np.random.default_rng(42).choice(len(tonal_samples),50000,replace=False)
            tonal_samples=tonal_samples[pick]
        count=min(16,len(centers)+2,len(np.unique(tonal_samples,axis=0)))
        cv2.setRNGSeed(42)
        _,_,tonal_centers=cv2.kmeans(tonal_samples,count,None,
            (cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,35,.2),3,cv2.KMEANS_PP_CENTERS)
        shades,_=assign(lab,tonal_centers)
        shades[~raster.valid]=0
        shades=sieve(shades,size=min(80,int(raster.valid.sum())),mask=raster.valid,connectivity=4)
    else:
        shades=cv2.resize(appearance.shades,(raster.rgb.shape[1],raster.rgb.shape[0]),interpolation=cv2.INTER_NEAREST)
    assignment, margin = assign(lab, centers)
    assignment[~raster.valid] = 0
    if appearance is None:
        shades = refine_fill_shades(lab,assignment,shades)
    checkpoint(folder, 'Сопоставляю исходные линии, цвет и текстуру…', 36 if appearance is None else 70)
    # Fill NoData before derivatives. No artificial black frame enters the cues.
    indices = ndimage.distance_transform_edt(~raster.valid, return_distances=False, return_indices=True)
    raw = cv2.cvtColor(raster.rgb[tuple(indices)], cv2.COLOR_RGB2LAB).astype(np.float32)
    light = raw[:, :, 0]
    ink = np.clip((cv2.GaussianBlur(light, (0, 0), 3) - light) / 35, 0, 1)
    gx = cv2.Sobel(light, cv2.CV_32F, 1, 0) / 8
    gy = cv2.Sobel(light, cv2.CV_32F, 0, 1) / 8
    xx = cv2.GaussianBlur(gx*gx, (0, 0), 2)
    yy = cv2.GaussianBlur(gy*gy, (0, 0), 2)
    xy = cv2.GaussianBlur(gx*gy, (0, 0), 2)
    coherence = np.sqrt((xx-yy)**2 + 4*xy*xy) / (xx+yy+1e-3)
    fine, wide = gradient(lab, .8*scale)*scale, gradient(lab, 2.5*scale)*scale
    # Retain density changes in printed texture as a separate, weak cue.
    texture = cv2.GaussianBlur(ink, (0, 0), 4)
    tx = cv2.Sobel(texture, cv2.CV_32F, 1, 0)/8
    ty = cv2.Sobel(texture, cv2.CV_32F, 0, 1)/8
    texture_edge = np.clip(np.hypot(tx, ty)*16, 0, 1)
    color_edge = np.clip(np.maximum(fine/8, wide/5), 0, 1)
    line = ink * coherence * (.3 + .7*np.clip(wide/3, 0, 1))
    # Long oriented strokes can separate equal-color units. Repeated hatch
    # strokes receive less weight when their local density is high.
    oriented = (ink*coherence).astype(np.float32)
    continuity = np.zeros_like(ink)
    for end in ((8,4),(4,8),(8,8),(8,0)):
        kernel=np.zeros((9,9),np.uint8)
        start=(0,4) if end==(8,4) else (4,0) if end==(4,8) else (0,0) if end==(8,8) else (0,8)
        cv2.line(kernel,start,end,1,1)
        continuity=np.maximum(continuity,cv2.morphologyEx(oriented,cv2.MORPH_OPEN,kernel))
    density=cv2.GaussianBlur(ink,(0,0),5)
    continuity*=np.clip(1-density*2, .2, 1)
    line=np.maximum(line,continuity)
    boundary = np.clip(.7*color_edge + .8*line + .25*texture_edge, 0, 1)
    boundary[~raster.valid] = 1
    colors_lab = centers.copy(); colors_lab[:, 0] /= .4
    colors = cv2.cvtColor(colors_lab.clip(0,255).astype(np.uint8)[None], cv2.COLOR_LAB2RGB)[0]
    palette=[]
    for i, rgb in enumerate(colors, 1):
        count=int((assignment==i).sum())
        palette.append(dict(id=i, color='#%02x%02x%02x'%tuple(rgb), rgb=rgb.tolist(),
                            share=round(100*count/int(raster.valid.sum()),1), enabled=True,
                            label=f'Заливка {i}', pixels=count))
    hatching = None
    texture_codes = None
    if appearance is None:
        checkpoint(folder, 'Ищу повторяющуюся штриховку…', 42)
        spectrum = spectral_grid(raster.rgb,raster.valid)
        texture_codes = print_codes(spectrum)
        hatching = hatch_patches(raster.rgb, raster.valid,spectrum)
        hatching = add_colour_hatching(raster.rgb, raster.valid, spectrum, hatching)
    prepared = Prepared(raster, lab, boundary.astype(np.float32), centers, assignment, margin, palette,scale=scale,line_evidence=continuity.astype(np.float32),shades=shades,overview=appearance,hatching=hatching,print_texture=texture_codes,grain=grain)
    if appearance is None and grain <= 2.5 and ink_mask.any():
        checkpoint(folder, 'Согласую цвет по обе стороны печатных штрихов…', 44)
        restored, count = consensus_colors(filled, ink_mask, raster.valid, reconstructed,
            check=lambda: checkpoint(folder, 'Согласую цвет по обе стороны печатных штрихов…', 44))
        if count and not np.array_equal(restored, clean):
            prepared.boundary_lab = lab
            prepared.lab = cv2.cvtColor(restored, cv2.COLOR_RGB2LAB).astype(np.float32)
            prepared.lab[:, :, 0] *= .4
            prepared.assignment, prepared.margin = assign(prepared.lab, centers)
            prepared.assignment[~raster.valid] = 0
            for swatch in palette:
                pixels = int((prepared.assignment == swatch['id']).sum())
                swatch.update(pixels=pixels, share=round(100*pixels/int(raster.valid.sum()), 1))
    checkpoint(folder, 'Палитра подготовлена', 45 if appearance is None else 92)
    return prepared


def validate_groups(prepared, groups):
    n = len(prepared.palette)
    if groups is None:
        return list(range(1,n+1))
    if (not isinstance(groups, list) or len(groups)!=n
            or any(type(v) is not int or v<0 or v>n for v in groups)):
        raise ValueError('Некорректный выбор групп заливок.')
    if not any(groups):
        raise ValueError('Оставьте хотя бы одну целевую заливку.')
    return groups


def region_partition(prepared, groups, folder):
    r=prepared.raster; valid=r.valid; labels=prepared.assignment
    checkpoint(folder, 'Выделяю внутренние области и согласую границы…', 50)
    markers=np.zeros(valid.shape,np.int32); seed_classes=[0]; seed_shades=[0]; offset=0
    pairs = labels.astype(np.uint16)*32 + prepared.shades
    for code in np.unique(pairs[valid]):
        cls,shade = divmod(int(code),32)
        checkpoint(folder, 'Согласую внутренние оттенки и общие границы…', 50+int(16*cls/len(groups)))
        domain=(pairs==code)&valid
        distance=ndimage.distance_transform_edt(domain)
        # Reserve uncertain border pixels for the watershed; only split interiors
        # at a persistent contact. A thin fill still gets a seed if it is coherent.
        reliable=domain & (prepared.boundary<.46) & (distance>=2*prepared.scale)
        pieces,count=ndimage.label(reliable)
        areas=np.bincount(pieces.ravel())
        keep=np.flatnonzero(areas>=12*prepared.scale**2);keep=keep[keep>0]
        lookup=np.zeros(count+1,np.int32)
        lookup[keep]=np.arange(offset+1,offset+len(keep)+1)
        local=lookup[pieces];markers[local>0]=local[local>0]
        seed_classes.extend([cls]*len(keep));seed_shades.extend([shade]*len(keep));offset+=len(keep)
        components,total=ndimage.label(domain)
        present=np.unique(components[markers>0]);present=present[present>0]
        sizes=np.bincount(components.ravel())
        missing=np.setdiff1d(np.flatnonzero(sizes>=6*prepared.scale**2),present);missing=missing[missing>0]
        if len(missing):
            positions=ndimage.maximum_position(distance+prepared.margin*.02,components,missing)
            for y,x in positions:
                offset+=1;markers[y,x]=offset;seed_classes.append(cls);seed_shades.append(shade)
    # Every disconnected NoData island must have a seed, including tiny masks.
    domains,total=ndimage.label(valid)
    unseeded=np.setdiff1d(np.arange(1,total+1),np.unique(domains[markers>0]))
    if len(unseeded):
        for y,x in ndimage.maximum_position(prepared.margin,domains,unseeded):
            offset+=1
            markers[y,x]=offset
            seed_classes.append(int(labels[y,x]))
            seed_shades.append(int(prepared.shades[y,x]))
    checkpoint(folder, 'Строю общую сеть областей…', 68)
    objects=watershed(prepared.boundary, markers, mask=valid, connectivity=1)
    seed_classes=np.asarray(seed_classes,np.uint8)
    # Merge pieces of the same selected appearance unless a strong continuous
    # boundary supports keeping them separate. Edges are shared by construction.
    parent=np.arange(offset+1)
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    pairs=[];strength=[]
    evidence=np.maximum(prepared.boundary,prepared.line_evidence) if prepared.line_evidence is not None else prepared.boundary
    for a,b,ea,eb in [(objects[:-1],objects[1:],evidence[:-1],evidence[1:]),
                       (objects[:,:-1],objects[:,1:],evidence[:,:-1],evidence[:,1:])]:
        edge=(a!=b)&(a>0)&(b>0)
        pairs.append(np.minimum(a[edge],b[edge]).astype(np.int64)*(offset+1)+np.maximum(a[edge],b[edge]))
        strength.append(np.maximum(ea[edge],eb[edge]))
    codes=np.concatenate(pairs);values=np.concatenate(strength)
    if len(codes):
        keys,inv=np.unique(codes,return_inverse=True);counts=np.bincount(inv)
        # A few dark marks can inflate the mean on an otherwise weak contact.
        # Include the fraction of the shared edge with strong supporting pixels.
        mean_strength = np.bincount(inv,weights=values)/counts
        strong_share = np.bincount(inv,weights=values>.65)/counts
        means = .85*mean_strength + .15*strong_share
        mapping=np.array([0]+groups)
        region_areas=np.bincount(objects.ravel(),minlength=offset+1)
        # Retain contact constraints during later merges. Otherwise a third
        # noisy piece can indirectly join both sides of a well-supported edge.
        blocked = [set() for _ in parent]
        candidates = []
        for key, mean, length in zip(keys, means, counts):
            left, right = divmod(int(key), offset+1)
            if mapping[seed_classes[left]] != mapping[seed_classes[right]]:
                continue
            threshold = .35 if seed_shades[left]!=seed_shades[right] else .43
            supported = (mean >= threshold and length >= 10*prepared.scale
                         and min(region_areas[left],region_areas[right]) >= 32*prepared.scale**2)
            if supported:
                blocked[left].add(right)
                blocked[right].add(left)
            else:
                candidates.append((mean,left,right))
        for _, left, right in sorted(candidates):
            left, right = find(left), find(right)
            if left==right or right in blocked[left]:
                continue
            if len(blocked[right]) > len(blocked[left]):
                left,right=right,left
            parent[right]=left
            for other in blocked[right]:
                blocked[other].discard(right)
                blocked[other].add(left)
            blocked[left].update(blocked[right])
            blocked[right].clear()
    lookup=np.array([find(i) for i in range(offset+1)],np.int32)
    objects=lookup[objects]
    # Polygonize connected raster pieces, removing isolated specks created by
    # the flood. Protected user vectors are overlaid after this entire stage.
    minimum = min(int(valid.sum()),max(2,round(6*prepared.scale**2)))
    objects=sieve(objects.astype(np.int32), size=minimum, mask=valid, connectivity=4)
    class_lookup = np.array([0]+[groups[int(cls)-1] for cls in seed_classes[1:]],np.uint16)
    return consolidate_fragments(objects, class_lookup, prepared, groups)


def consolidate_fragments(objects, classes, prepared, groups):
    """Absorb compact specks with one dominant, weakly marked contact."""
    regions = connected_labels(objects, background=0, connectivity=1).astype(np.int32)
    count = int(regions.max()) + 1
    ids, first = np.unique(regions, return_index=True)
    unit = np.zeros(count, dtype=classes.dtype)
    unit[ids] = classes[objects.ravel()[first]]
    area = np.bincount(regions.ravel(), minlength=count)
    bounds = ndimage.find_objects(regions)
    strength = np.maximum(prepared.boundary, prepared.line_evidence)
    codes, values = [], []
    for a, b, x, y in ((regions[:-1], regions[1:], strength[:-1], strength[1:]),
                       (regions[:,:-1], regions[:,1:], strength[:,:-1], strength[:,1:])):
        cut = (a != b) & (a > 0) & (b > 0)
        low = np.minimum(a[cut], b[cut]).astype(np.int64)
        codes.append(low * count + np.maximum(a[cut], b[cut]))
        values.append(np.maximum(x[cut], y[cut]))
    keys, inverse, lengths = np.unique(np.concatenate(codes), return_inverse=True, return_counts=True)
    strong = np.bincount(inverse, weights=np.concatenate(values) >= .45)
    left, right = np.divmod(keys, count)
    perimeter = np.bincount(left, weights=lengths, minlength=count) + np.bincount(right, weights=lengths, minlength=count)
    means = np.stack([np.bincount(regions.ravel(), weights=channel.ravel(), minlength=count)
                      / np.maximum(area, 1) for channel in prepared.lab.transpose(2,0,1)], axis=1)
    ambiguous = np.bincount(regions.ravel(), weights=(prepared.margin < 4).ravel(), minlength=count)
    automatic = not prepared.sampled and groups == list(range(1, len(groups)+1))
    common = {swatch['id'] for swatch in prepared.palette if swatch['share'] >= 2.}
    border = np.zeros(count, bool)
    border[np.unique(regions[prepared.raster.valid & ~ndimage.binary_erosion(prepared.raster.valid)])] = True
    lookup = np.arange(count, dtype=np.int32)
    # Target regions exceed the upper size limit for a source, avoiding cascaded merges.
    for a, b, length, ink in zip(left, right, lengths, strong):
        small, large = (a, b) if area[a] < area[b] else (b, a)
        if (area[small] > 150 or area[large] <= 150
                or border[small] or length < .8*perimeter[small] or ink > .35*length
                or np.linalg.norm(means[small]-means[large]) > 8):
            continue
        if unit[small] != unit[large] and not (
                automatic and unit[small] in common and ambiguous[small] >= .6*area[small]):
            continue
        rows, cols = bounds[small-1]
        longest = max(rows.stop-rows.start, cols.stop-cols.start)
        # Preserve lenses, ribbons and oblique strands with a long, narrow footprint.
        if longest*longest <= 4*area[small]:
            lookup[small] = large
    return lookup[regions], unit


def _contact_colour_model(base, prepared):
    """Read native paper inside each region; exclude ink and uncertain rims.

    Keep the colour surface in byte Lab. Only the small region models and
    candidate batches use floats, including the existing 0.4 lightness weight.
    """
    rgb = getattr(prepared.raster, 'rgb', None)
    if rgb is None:
        return None
    raw = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    light = raw[:, :, 0]
    local = cv2.dilate(light, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    readable = (cv2.subtract(local, light) <= 12) & prepared.raster.valid
    colours = cv2.medianBlur(raw, 3)
    core = np.zeros(base.shape, bool)
    center = base[1:-1, 1:-1]
    core[1:-1, 1:-1] = ((center > 0) & (center == base[:-2, 1:-1])
                         & (center == base[2:, 1:-1]) & (center == base[1:-1, :-2])
                         & (center == base[1:-1, 2:]))
    core &= readable & (prepared.boundary < .4)
    count = int(base.max()) + 1
    ids = base[core]
    sizes = np.bincount(ids, minlength=count)
    supported = sizes >= max(12, round(12*prepared.scale**2))
    pixels = colours[core]
    models = np.stack([np.bincount(ids, weights=pixels[:, i], minlength=count)
                       / np.maximum(1, sizes) for i in range(3)], axis=1).astype(np.float32)
    models[:, 0] *= .4
    return colours, readable, models, supported


def refine_contacts(objects, prepared, folder=None):
    """Move only a narrow contact corridor, keeping an anchor for each thin structure."""
    checkpoint(folder, 'Уточняю положение контактов по исходным штрихам…', 76)
    valid = prepared.raster.valid
    height, width = valid.shape
    base = cv2.resize(objects, (width, height), interpolation=cv2.INTER_NEAREST_EXACT)
    # The native analysis mask has a margin for exact clipping in project coordinates.
    if np.any(valid & (base == 0)):
        nearest = ndimage.distance_transform_edt(base == 0, return_distances=False, return_indices=True)
        missing = valid & (base == 0)
        base[missing] = base[nearest[0][missing], nearest[1][missing]]
    base[~valid] = 0
    edges = np.zeros(valid.shape, bool)
    for a, b, near, far in ((base[:-1], base[1:], edges[:-1], edges[1:]),
                            (base[:,:-1], base[:,1:], edges[:,:-1], edges[:,1:])):
        cut = (a != b) & (a > 0) & (b > 0)
        near |= cut
        far |= cut
    if not edges.any():
        return base
    # Optimize only simple boundary pixels. The cyclic sequence around such a
    # pixel has two contiguous arcs (one per region). This forbids new islands,
    # broken necks, filled holes and movement of multi-region junctions.
    offsets = ((-1,0),(-1,1),(0,1),(1,1),(1,0),(1,-1),(0,-1),(-1,-1))
    radius = max(1, round(1.5*prepared.scale))
    band = ndimage.binary_dilation(edges, iterations=radius) & ndimage.binary_erosion(valid)
    band[[0,-1],:] = False
    band[:,[0,-1]] = False
    rows, cols = np.nonzero(band)
    parity = 2*(rows % 2)+cols % 2
    # Bound the memory occupied by the eight neighbours of each candidate.
    phases = []
    for phase in range(4):
        y, x = rows[parity == phase], cols[parity == phase]
        for start in range(0, len(y), 65536):
            phases.append((y[start:start+65536], x[start:start+65536]))
    colour_model = _contact_colour_model(base, prepared)
    current = base.copy()
    for step in range(radius+1):
        checkpoint(folder, 'Уточняю положение контактов по исходным штрихам…', 76+4*step//radius)
        moves = 0
        for y, x in phases:
            ring = np.stack([current[y+dy,x+dx] for dy,dx in offsets])
            old = current[y,x]
            same = ring == old
            # Each label must touch a face of the central pixel. Keep line tips.
            faces = same[::2].sum(axis=0)
            possible = (faces == 2) | (faces == 3)
            changes = (ring != np.roll(ring,1,axis=0)).sum(axis=0)
            candidate = ring[np.argmax(~same,axis=0), np.arange(len(x))]
            possible &= (changes == 2) & (candidate > 0)
            # A positive gain puts more boundary length on supported ink edges.
            cost = np.stack([1. - .85*np.maximum(prepared.boundary[y,x], prepared.boundary[y+dy,x+dx])
                             for dy,dx in offsets[::2]])
            gain = (cost*(1-2*same[::2].astype(np.int8))).sum(axis=0)
            gain += .14*((old != base[y,x]).astype(np.float32) - (candidate != base[y,x]))
            if colour_model is not None:
                colours, readable, models, supported = colour_model
                eligible = (readable[y, x] & supported[old] & supported[candidate]
                            & (np.linalg.norm(models[old]-models[candidate], axis=1) >= 3))
                if eligible.any():
                    pixels = colours[y[eligible], x[eligible]].astype(np.float32)
                    pixels[:, 0] *= .4
                    previous = np.linalg.norm(pixels-models[old[eligible]], axis=1)
                    proposed = np.linalg.norm(pixels-models[candidate[eligible]], axis=1)
                    # Native exposed colour is a bounded extra vote. Ink,
                    # unsupported small regions and unrelated glyph colours
                    # retain the original line/geometry/prior decision.
                    gain[eligible] += (.2*np.clip((previous-proposed)/8, -1, 1)
                                       * (np.minimum(previous, proposed) <= 10))
            take = possible & (gain > .02)
            current[y[take],x[take]] = candidate[take]
            moves += int(take.sum())
        if not moves:
            break
    return current


def segment_prepared(prepared, groups=None, folder=None):
    groups = validate_groups(prepared, groups)
    overview = prepared.overview or prepared
    objects, class_lookup = region_partition(overview, groups, folder)
    if overview.hatching is not None:
        objects, class_lookup = fit_hatching(
            objects, class_lookup, overview.hatching, overview,
            automatic_palette=not overview.sampled and groups == list(range(1, len(groups)+1)))
    contact_checks = []
    if overview.print_texture is not None and not overview.sampled and groups==list(range(1,len(groups)+1)):
        checkpoint(folder,'Проверяю общие линии близких заливок…',75)
        objects,class_lookup,contact_checks = reconcile_contacts(objects,class_lookup,overview,match_paper=True)
        objects,class_lookup = split_interior_contacts(objects,class_lookup,overview,
            check=lambda: checkpoint(folder,'Проверяю внутренние контакты близких заливок…',75))
    # Texture fitting and contact reconciliation may cut or relabel a region
    # after the initial fragment checks. Recheck the resulting partition using
    # the same colour, confidence, ink and slender-shape protections.
    checkpoint(folder, 'Перепроверяю мелкие фрагменты заливок…', 75)
    objects,class_lookup = consolidate_fragments(objects,class_lookup,overview,groups)
    if overview is not prepared:
        objects = refine_contacts(objects, prepared, folder)
    r = prepared.raster
    valid = r.valid
    colors = [p['rgb'] for p in prepared.palette]
    class_image = class_lookup[objects]
    checkpoint(folder, 'Согласую общие контуры…', 82)
    from .automatic import polygon_parts
    import shapely
    from .editing import transform_geometry
    pixel_polygons=[]
    classes=[]
    for geo,oid in shapes(objects,mask=class_image>0,connectivity=4):
        geometry=shape(geo)
        if not geometry.is_valid:
            geometry=shapely.make_valid(geometry)
        for part in polygon_parts(geometry):
            pixel_polygons.append(part)
            classes.append(int(class_lookup[int(oid)]))
    pixel_polygons,simplified=simplify_partition(pixel_polygons,prepared.scale)
    features=[]
    for oid,(part,cls) in enumerate(zip(pixel_polygons,classes),1):
        geometry=transform_geometry(part,r.transform)
        if not geometry.is_valid:
            raise ValueError('Не удалось согласовать геометрию контуров.')
        features.append(dict(geometry=geometry,properties=dict(object_id=oid,class_id=cls,
            note='',review='unreviewed',origin='joint_quality')))
    if simplified and features:
        class_image=rasterize([(f['geometry'],f['properties']['class_id']) for f in features],
            out_shape=valid.shape,transform=r.transform,dtype='uint16')
        class_image[~valid]=0
    uncertainty=(prepared.margin<3)&valid
    uncertain_share=round(float(uncertainty.sum()/valid.sum()*100),1)
    checkpoint(folder, 'Контуры построены', 88)
    return Result(r,class_image,features,colors),dict(contact_checks=contact_checks,uncertain_share=uncertain_share,groups=len(set(groups)-{0}),
        pixels=int(valid.sum()),source_note=prepared.source_note, simplified=simplified)


def simplify_partition(polygons,scale):
    """Simplify the shared coverage in pixel units, fixing the entire exterior.

    Roll back the candidate if it moves any boundary too far, removes holes or
    substantially changes areas. Densifying pixel edges inserts shared T nodes.
    """
    import shapely
    if not polygons or not hasattr(shapely,'coverage_simplify') or shapely.geos_version < (3,12,0):
        return polygons,False
    dense=shapely.segmentize(polygons,1.)
    if not shapely.coverage_is_valid(dense):
        return polygons,False
    original_area=shapely.area(dense)
    holes=shapely.get_num_interior_rings(dense)
    for tolerance in (.78*scale, .55*scale, .38*scale):
        candidate=shapely.coverage_simplify(dense,tolerance,simplify_boundary=False)
        if not (shapely.is_valid(candidate).all() and shapely.coverage_is_valid(candidate)):
            return polygons,False
        displacement=shapely.hausdorff_distance(shapely.boundary(dense),shapely.boundary(candidate))
        unstable=((shapely.get_num_interior_rings(candidate)!=holes) | (displacement>scale)
                  | (np.abs(shapely.area(candidate)-original_area)>np.maximum(.5,original_area*.035)))
        if not unstable.any():
            if shapely.get_num_coordinates(candidate).sum()<shapely.get_num_coordinates(polygons).sum():
                return candidate,True
            break
    return polygons,False


def analysis_raster(working):
    """Use a matching source when available; keep the project's footprint fixed.

    Embedded projects remain usable offline. Above 12 MP the analysis is bounded
    explicitly; the UI reports the actual analysis size rather than claiming native.
    """
    from dataclasses import replace
    import rasterio
    from .core import read_raster
    if not Path(working.path).is_file():
        return working, 'Встроенный растр; исходный TIFF недоступен'
    try:
        check=read_raster(working.path)
        if (check.transform!=working.transform or check.crs_wkt!=working.crs_wkt
                or not np.array_equal(check.rgb,working.rgb)
                or not np.array_equal(check.valid, working.source_valid if working.source_valid is not None else working.valid)):
            return working, 'Встроенный растр; исходный TIFF изменился'
        with rasterio.open(working.path) as src:
            factor=min(1.,math.sqrt(12_000_000/(src.width*src.height)))
            side=max(1,int(max(src.width,src.height)*factor))
        original=read_raster(working.path,max_side=side)
        if original.rgb.shape==working.rgb.shape:
            return working, 'Полное доступное разрешение'
        # Extend only a narrow edge belt before exact world-space clipping.
        valid=cv2.resize(working.valid.astype(np.uint8),(original.rgb.shape[1],original.rgb.shape[0]),
                         interpolation=cv2.INTER_NEAREST)>0
        valid=ndimage.binary_dilation(valid,iterations=2)
        indices=ndimage.distance_transform_edt(~original.valid,return_distances=False,return_indices=True)
        filled=original.rgb[tuple(indices)]
        note='Исходный TIFF' if factor==1 else 'Анализ ограничен 12 млн пикселей для памяти'
        return replace(original, rgb=filled, valid=valid, area=working.area,
                       source_valid=original.valid if working.area is not None else None, area_name=working.area_name),note
    except (OSError,ValueError,rasterio.errors.RasterioError):
        return working, 'Встроенный растр; исходный TIFF не прочитан'


def to_working(result, working, all_groups=True):
    from .automatic import polygon_parts
    from shapely.ops import unary_union
    if (working.area is None and result.raster.transform==working.transform and np.array_equal(result.raster.valid,working.valid)):
        return Result(working,result.labels,result.features,result.colors)
    from .region import footprint as raster_footprint
    footprint = raster_footprint(working)
    features=[]
    for f in result.features:
        for geometry in polygon_parts(f['geometry'].intersection(footprint)):
            if not geometry.is_valid:
                raise ValueError('Не удалось согласовать границу области карты.')
            props=dict(f['properties'],object_id=len(features)+1)
            features.append(dict(geometry=geometry,properties=props))
    if all_groups:
        covered=unary_union([f['geometry'] for f in features])
        tolerance=abs(working.transform.determinant)*max(1e-6,working.valid.sum()*1e-10)
        if covered.symmetric_difference(footprint).area>tolerance:
            raise ValueError('Нарушено покрытие при переносе результата. Прежние данные сохранены.')
    labels=rasterize([(f['geometry'],f['properties']['class_id']) for f in features],
        out_shape=working.valid.shape,transform=working.transform,dtype='uint16') if features else np.zeros(working.valid.shape,np.uint16)
    labels[~working.valid]=0
    return Result(working,labels,features,result.colors)


_cache = None


def prepare_job(working, token, folder):
    global _cache
    from PIL import Image
    _cache = None
    checkpoint(folder,'Читаю подробное изображение…',1)
    raster,note=analysis_raster(working)
    overview=prepare(working,folder)
    prepared=overview if raster is working else prepare(raster,folder,appearance=overview)
    if overview.grain > 2.5:
        note += ' · восстановлены заливки зернистого скана'
    prepared.source_note=note
    _cache=(token,prepared,working)
    np.save(Path(folder)/'palette-lab.npy',overview.lab,allow_pickle=False)
    assignment=overview.assignment
    np.save(Path(folder)/'palette-labels.npy',assignment,allow_pickle=False)
    boundary=cv2.resize(prepared.boundary,(working.rgb.shape[1],working.rgb.shape[0]),interpolation=cv2.INTER_AREA)
    rgba=np.zeros((*working.valid.shape,4),np.uint8);rgba[:,:,:3]=[244,135,25]
    rgba[:,:,3]=(np.clip(boundary*210,0,210)*working.valid).astype(np.uint8)
    Image.fromarray(rgba).save(Path(folder)/'quality-evidence.png')
    checkpoint(folder,'Подготовка завершена',100)
    return dict(palette=overview.palette,width=raster.rgb.shape[1],height=raster.rgb.shape[0],source_note=note)


def quality_job(working, previous, token, groups, folder, samples=(), palette=None):
    from .automatic import preserve_work
    from .core import save_overlay
    if _cache is None or _cache[0]!=token:
        raise ValueError('Подготовка карты устарела. Повторите анализ.')
    from .palette import sampled_preparation
    from .review import review_sites
    prepared=sampled_preparation(_cache[1],samples,palette)
    result,info=segment_prepared(prepared,groups,folder)
    checkpoint(folder,'Согласую точные ручные правки…',90)
    result=to_working(result,working,all_groups=all(groups))
    result=preserve_work(result,previous,partial=not all(groups))
    overview = prepared.overview or prepared
    info['sites'] = review_sites(result,overview.boundary,overview.margin,info.pop('contact_checks'))
    checkpoint(folder,'Отрисовываю результат…',96)
    save_overlay(result,Path(folder)/'auto-preview.png')
    checkpoint(folder,'Предпросмотр готов',100)
    return result,info
