"""Reproduce the fixed baseline/scan diagnostic on prepared data.npz.

Reference labels are used only for scoring, not segmentation. Development IDs
were used to choose the scan recipe; remaining objects are from the same sheet,
not an independent geographic test set. No parameter search is performed here.
"""
import argparse
import json
from pathlib import Path
import time

from affine import Affine
import numpy as np
from scipy import ndimage

from geotrace.core import Raster, segment, save_overlay

DEVELOPMENT = {1, 2, 14, 75}


def components(labels, valid):
    objects = np.zeros(valid.shape, np.int32)
    offset = 0
    for cls in np.unique(labels):
        if not cls:
            continue
        connected, count = ndimage.label((labels == cls) & valid)
        present = connected > 0
        objects[present] = connected[present] + offset
        offset += count
    return objects, offset


def object_scores(objects, reference, evaluation, ids):
    areas = np.bincount(objects[evaluation], minlength=int(objects.max()) + 1)
    scores = []
    for oid in ids:
        target = (reference == oid) & evaluation
        overlaps = np.bincount(objects[target], minlength=len(areas))
        overlaps[0] = 0
        ious = overlaps / np.maximum(1, areas + target.sum() - overlaps)
        scores.append(dict(id=int(oid), pixels=int(target.sum()), iou=float(ious.max())))
    return scores


def boundary(labels, interior):
    edges = np.zeros(labels.shape, bool)
    edges[1:] |= labels[1:] != labels[:-1]
    edges[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    return edges & interior


def benchmark(data, output):
    output.mkdir(parents=True, exist_ok=True)
    with np.load(data, allow_pickle=False) as source:
        rgb, valid, reference = source['rgb'], source['valid'], source['reference']
        transform = Affine(*source['transform'])
    raster = Raster(str(data), rgb, valid, transform, '', '', (rgb.shape[1], rgb.shape[0]))
    evaluation = valid & (reference > 0)
    ids = [int(i) for i in np.unique(reference) if i not in DEVELOPMENT | {0}
           and ((reference == i) & evaluation).sum() >= 256]
    interior = ndimage.binary_erosion(evaluation, iterations=4)
    truth = boundary(reference, interior)
    report = dict(working_size=list(valid.shape[::-1]), groups=7, min_pixels=80,
                  development_ids=sorted(DEVELOPMENT), heldout_ids=ids,
                  note='Same-sheet diagnostic, not external validation. Best single-component IoU; boundary F1 over all known sheet coverage, tolerance 3 working pixels.',
                  profiles={})
    for profile in ('baseline', 'scan'):
        start = time.perf_counter()
        result = segment(raster, 7, 80, profile)
        elapsed = time.perf_counter() - start
        objects, count = components(result.labels, valid)
        cases = object_scores(objects, reference, evaluation, ids)
        predicted = boundary(objects, interior)
        precision = float((ndimage.distance_transform_edt(~truth)[predicted] <= 3).mean()) if predicted.any() else 0.
        recall = float((ndimage.distance_transform_edt(~predicted)[truth] <= 3).mean()) if truth.any() and predicted.any() else 0.
        report['profiles'][profile] = dict(seconds=elapsed, polygons=len(result.features), components=count,
            cases=cases, mean_iou=float(np.mean([c['iou'] for c in cases])),
            median_iou=float(np.median([c['iou'] for c in cases])), boundary_precision=precision,
            boundary_recall=recall, boundary_f1=2*precision*recall/(precision+recall) if precision+recall else 0.)
        save_overlay(result, output/f'{profile}-overlay.png')
    a, b = [report['profiles'][k]['cases'] for k in ('baseline', 'scan')]
    delta = np.array([y['iou']-x['iou'] for x,y in zip(a,b)])
    report.update(improved=int((delta > 1e-6).sum()), regressed=int((delta < -1e-6).sum()),
                  unchanged=int((np.abs(delta) <= 1e-6).sum()))
    (output/'auto-metrics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='data.npz produced by prepare_reference.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.data, args.output)
    print(json.dumps({k:{m:v for m,v in p.items() if m!='cases'} for k,p in report['profiles'].items()},indent=2))
