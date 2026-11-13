"""Run each method in a fresh process against prepared, fixed reference cases.

Example: python tools/benchmark_prompts.py --data /path/to/benchmark --method color
Reference-derived prompts make this an assisted diagnostic, NOT an automatic accuracy estimate.
"""
import argparse
import json
from pathlib import Path
try:
    import resource
except ImportError:  # Windows: research extra supplies psutil for the sampling fallback.
    resource = None
import threading
import sys
from time import perf_counter

import cv2
import numpy as np
from affine import Affine
from scipy import ndimage

from geotrace.core import Raster, segment
from geotrace.prompts import propose


def metrics(mask, target, valid):
    mask, target = mask & valid, target & valid
    intersection, union = (mask & target).sum(), (mask | target).sum()
    a = mask ^ ndimage.binary_erosion(mask)
    b = target ^ ndimage.binary_erosion(target)
    # Do not score crop edge or NoData boundary as a geological boundary.
    interior = ndimage.binary_erosion(valid, iterations=4, border_value=0)
    a &= interior; b &= interior
    precision = float((ndimage.distance_transform_edt(~b)[a] <= 3).mean()) if a.any() and b.any() else 0
    recall = float((ndimage.distance_transform_edt(~a)[b] <= 3).mean()) if a.any() and b.any() else 0
    return {'iou': float(intersection/union) if union else 1.,
            'boundary_f1_3px': 2*precision*recall/(precision+recall) if precision+recall else 0.}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--method',choices=['baseline','color','mobilesam'],required=True)
    parser.add_argument('--checkpoint')
    args=parser.parse_args()
    peak_samples=[]
    stop=threading.Event()
    if resource is None:
        import psutil
        process=psutil.Process()
        def monitor():
            while not stop.wait(.01):
                peak_samples.append(process.memory_info().rss)
        monitor_thread=threading.Thread(target=monitor,daemon=True)
        monitor_thread.start()
    cv2.setNumThreads(2)
    data=np.load(args.data/'data.npz')
    r=Raster('benchmark',data['rgb'],data['valid'],Affine(*data['transform']), '', '',
             (data['rgb'].shape[1],data['rgb'].shape[0]))
    cases=json.loads((args.data/'cases.json').read_text())
    rows=[]; masks=[]
    for case in cases:
        x0,y0,x1,y1=case['roi']
        target=data['reference'][y0:y1,x0:x1]==case['id']
        valid=data['valid'][y0:y1,x0:x1] & (data['reference'][y0:y1,x0:x1]>0)
        start=perf_counter()
        if args.method=='baseline':
            cropped=Raster('benchmark crop',r.rgb[y0:y1,x0:x1],r.valid[y0:y1,x0:x1],
                           r.transform*Affine.translation(x0,y0),'','',(x1-x0,y1-y0))
            result=segment(cropped,7,80)
            x,y,_=case['points'][0]
            cls=result.labels[y-y0,x-x0]
            components,_=ndimage.label(result.labels==cls)
            prediction=components==components[y-y0,x-x0]
        else:
            proposal=propose(r,case['roi'],case['points'],args.method,checkpoint=args.checkpoint)
            prediction=proposal.mask[y0:y1,x0:x1]
        elapsed=perf_counter()-start
        rows.append({'case':case['name'],'seconds':elapsed,**metrics(prediction,target,valid)})
        masks.append(prediction)
        print(args.method,rows[-1],flush=True)
    if resource is not None:
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mib=peak/(1024**2 if sys.platform=='darwin' else 1024)
    else:
        stop.set(); monitor_thread.join()
        peak_mib=max(peak_samples,default=process.memory_info().rss)/1024**2
    report={'method':args.method,'cpu_threads':2,'peak_process_rss_mib':peak_mib,
            'memory_measurement':'ru_maxrss' if resource is not None else 'sampled_RSS_10ms','cases':rows,
            'mean_iou':float(np.mean([r['iou'] for r in rows])),
            'mean_boundary_f1_3px':float(np.mean([r['boundary_f1_3px'] for r in rows]))}
    (args.data/(args.method+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    np.savez_compressed(args.data/(args.method+'.npz'),masks=np.array(masks))
    print('SUMMARY',json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
