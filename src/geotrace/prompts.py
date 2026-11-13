"""Bounded, prompt-guided proposals. Optional neural imports stay out of normal startup."""
from dataclasses import dataclass
from pathlib import Path
import hashlib

import cv2
import numpy as np
from scipy import ndimage

from .core import Raster


@dataclass
class Proposal:
    mask: np.ndarray
    roi: tuple
    method: str
    touches_edge: bool


def roi_at(raster, x, y, size=512):
    h, w = raster.valid.shape
    size = max(128, min(768, int(size)))
    rw, rh = min(w, size), min(h, size)
    x0 = max(0, min(w - rw, int(x) - rw // 2))
    y0 = max(0, min(h - rh, int(y) - rh // 2))
    return x0, y0, x0 + rw, y0 + rh


def validate_points(raster, roi, points):
    x0, y0, x1, y1 = roi
    h, w = raster.valid.shape
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h and x1-x0 <= 768 and y1-y0 <= 768):
        raise ValueError('Область анализа должна находиться на карте и быть не больше 768 × 768 px.')
    if not points or not any(int(p[2]) == 1 for p in points):
        raise ValueError('Поставьте хотя бы одну точку «Включить» внутри нужной области.')
    if len(points) > 64:
        raise ValueError('Можно задать до 64 подсказок для одной области.')
    seen = {}
    for x, y, label in points:
        x, y = int(x), int(y)
        if label not in (0, 1) or not (x0 <= x < x1 and y0 <= y < y1) or not raster.valid[y, x]:
            raise ValueError('Подсказки должны находиться внутри рамки анализа и непрозрачной части карты.')
        if (x, y) in seen and seen[x, y] != label:
            raise ValueError('Одна точка не может одновременно включаться и исключаться.')
        seen[x, y] = label
    return np.array([(int(x)-x0, int(y)-y0, int(v)) for x, y, v in points], dtype=np.int32)


def clean_colors(rgb, valid):
    """Local bright-paper reconstruction; may erase narrow genuine dark units."""
    if not valid.any():
        raise ValueError('В области нет данных.')
    # Fill transparency before filtering so black frame cannot create a false boundary.
    indices = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = rgb[tuple(indices)]
    lab = cv2.cvtColor(filled, cv2.COLOR_RGB2LAB)
    light = lab[:, :, 0]
    local_light = ndimage.percentile_filter(light, 70, size=9)
    ink = (light.astype(np.int16) < local_light.astype(np.int16) - 24) & valid
    if ink.any() and (~ink & valid).any():
        filled = cv2.inpaint(filled, ink.astype(np.uint8)*255, 3, cv2.INPAINT_TELEA)
    return cv2.medianBlur(filled, 5)


def color_mask(rgb, valid, points, tolerance=22):
    """Color distance plus explicit foreground/background seeds and connected-component constraint."""
    cleaned = clean_colors(rgb, valid)
    lab = cv2.cvtColor(cleaned, cv2.COLOR_RGB2LAB).astype(np.float32)
    # Less weight on paper brightness, more on chromaticity.
    lab[:, :, 0] *= 0.4
    def distance(label):
        samples = []
        for x, y, value in points:
            if value == label:
                patch = lab[max(0,y-2):y+3, max(0,x-2):x+3]
                samples.append(np.median(patch.reshape(-1,3), axis=0))
        dist = np.full(valid.shape, np.inf, np.float32)
        for sample in samples:
            dist = np.minimum(dist, np.sqrt(np.sum((lab-sample)**2, axis=2)))
        return dist
    foreground = distance(1)
    background = distance(0)
    mask = (foreground <= tolerance) & (foreground + 1.5 < background) & valid
    # Close only small print breaks; hard hints are re-applied afterwards.
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5))).astype(bool) & valid
    for x,y,value in points:
        cv2.circle(mask.view(np.uint8),(int(x),int(y)),2,int(value),-1)
    return mask & valid


_model = None
_predictor = None
_weights_key = None
_image_key = None


def neural_mask(rgb, valid, points, checkpoint):
    global _model, _predictor, _weights_key, _image_key
    import torch
    from mobile_sam import sam_model_registry, SamPredictor
    path = Path(checkpoint).resolve()
    key = (str(path), path.stat().st_mtime_ns)
    torch.set_num_threads(2)
    if key != _weights_key:
        _model = sam_model_registry['vit_t']()
        _model.load_state_dict(torch.load(path, map_location='cpu', weights_only=True))
        _model.to('cpu').eval()
        _predictor = SamPredictor(_model)
        _weights_key, _image_key = key, None
    image_key = hashlib.sha256(rgb.tobytes()).digest()
    with torch.inference_mode():
        if image_key != _image_key:
            _predictor.set_image(rgb)
            _image_key = image_key
        masks, scores, _ = _predictor.predict(point_coords=points[:, :2], point_labels=points[:, 2],
                                               multimask_output=True)
    # Choose using model confidence and prompt compliance, NEVER reference IoU.
    compliance = np.array([sum(bool(m[y,x]) == bool(v) for x,y,v in points) for m in masks])
    best = max(range(len(masks)), key=lambda i: (compliance[i], float(scores[i])))
    return masks[best] & valid


def propose(raster, roi, points, method='color', tolerance=22, checkpoint=None):
    cv2.setNumThreads(2)
    local = validate_points(raster, roi, points)
    x0,y0,x1,y1 = roi
    rgb = raster.rgb[y0:y1,x0:x1].copy()
    valid = raster.valid[y0:y1,x0:x1]
    if method == 'color':
        mask = color_mask(rgb, valid, local, tolerance)
    elif method == 'mobilesam':
        if not checkpoint:
            raise ValueError('MobileSAM не настроен. Используйте метод «Цвет + подсказки».')
        mask = neural_mask(rgb, valid, local, checkpoint)
    else:
        raise ValueError('Неизвестный метод выделения.')
    # Guarantee hard hints, then keep only components attached to positive points.
    for x,y,value in local:
        mask[y,x] = bool(value)
    components, _ = ndimage.label(mask)
    wanted = [components[y,x] for x,y,v in local if v == 1]
    mask = np.isin(components, [i for i in wanted if i]) & valid
    touches = bool(mask[0].any() or mask[-1].any() or mask[:,0].any() or mask[:,-1].any())
    full = np.zeros(raster.valid.shape, dtype=bool)
    full[y0:y1,x0:x1] = mask
    return Proposal(full, tuple(roi), method, touches)
