"""Run the current automatic method; score a separately aligned reference if supplied."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage
import shapely

from benchmark_auto import object_scores, boundary
from geotrace import __version__, quality
from geotrace.core import read_raster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raster', type=Path, required=True)
    parser.add_argument('--reference', type=Path, help='Integer NPY labels aligned to the working raster, 0 = unknown')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    raster = read_raster(args.raster)
    reference = np.load(args.reference, allow_pickle=False) if args.reference else None
    if reference is not None:
        if reference.shape != raster.valid.shape or reference.dtype.kind not in 'iu' or np.any(reference < 0):
            parser.error('reference must contain nonnegative integer labels in the working raster grid')
    prepared = quality.prepare(raster)
    native, note = quality.analysis_raster(raster)
    fine = prepared if native is raster else quality.prepare(native, appearance=prepared)
    result, _ = quality.segment_prepared(fine)
    result = quality.to_working(result, raster)
    geometries = [feature['geometry'] for feature in result.features]
    union = shapely.union_all(geometries)
    expected = raster.valid.sum() * abs(raster.transform.determinant)
    tolerance = max(1e-12, expected * 1e-8)
    report = dict(version=__version__, working_size=list(raster.valid.shape[::-1]),
                  polygons=len(geometries), groups=len(prepared.palette), source_note=note,
                  valid_geometry=all(g.is_valid for g in geometries),
                  complete_coverage=bool(abs(union.area - expected) <= tolerance),
                  no_overlaps=bool(abs(sum(g.area for g in geometries) - union.area) <= tolerance))
    objects = rasterize([(g, i+1) for i, g in enumerate(geometries)], out_shape=raster.valid.shape,
                        transform=raster.transform, dtype='int32') if geometries else np.zeros(raster.valid.shape, np.int32)
    if reference is not None:
        mask = raster.valid & (reference > 0)
        ids = [int(k) for k in np.unique(reference[mask]) if ((reference == k) & mask).sum() >= 256]
        scores = object_scores(objects, reference, mask, ids)
        interior = ndimage.binary_erosion(mask, iterations=4)
        truth, edges = boundary(reference, interior), boundary(objects, interior)
        precision = float((ndimage.distance_transform_edt(~truth)[edges] <= 3).mean()) if truth.any() and edges.any() else 0.
        recall = float((ndimage.distance_transform_edt(~edges)[truth] <= 3).mean()) if truth.any() and edges.any() else 0.
        report.update(reference_objects=len(ids), cases=scores,
                      mean_iou=float(np.mean([s['iou'] for s in scores])) if scores else None,
                      boundary_f1=2*precision*recall/(precision+recall) if precision+recall else 0.,
                      boundary_precision=precision, boundary_recall=recall,
                      scoring='Best single-polygon IoU, equal object weights, min 256 working pixels; boundary tolerance 3 working pixels')
    report['seconds'] = time.perf_counter() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Redirected Windows consoles may still use a legacy single-byte encoding.
    # Keep stdout portable ASCII JSON; the saved report remains readable UTF-8.
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=True))
    if not all(report[k] for k in ('valid_geometry', 'complete_coverage', 'no_overlaps')):
        raise SystemExit('Geometry validation failed; see report')


if __name__ == '__main__':
    main()
