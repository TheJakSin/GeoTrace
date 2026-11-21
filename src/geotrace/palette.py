"""User samples split a single existing fill in the reconstructed colour image."""
import numpy as np
from scipy import ndimage
from dataclasses import replace


def sample_fill(lab, labels, x, y, number):
    h, w = labels.shape
    if not (0 <= x < w and 0 <= y < h) or not labels[y,x]:
        raise ValueError('Выберите заливку внутри карты.')
    parent = int(labels[y,x])
    roi = np.s_[max(0,y-2):y+3,max(0,x-2):x+3]
    picked = np.median(lab[roi][labels[roi] == parent],axis=0)
    pixels = lab[labels == parent]
    if len(pixels) < 8:
        raise ValueError('В этой группе слишком мало пикселей для разделения.')
    representative = np.median(pixels,axis=0)
    if np.linalg.norm(representative-picked) < 2:
        # Clicking the dominant shade should work too. Estimate a second shade
        # from uniform interior pixels so scan noise cannot form its own group.
        different = np.zeros(labels.shape, bool)
        different[labels == parent] = np.linalg.norm(pixels-picked, axis=1) >= 3
        interior = lab[ndimage.binary_erosion(different, structure=np.ones((3,3),bool))]
        if len(interior) < 4:
            raise ValueError('Отдельного оттенка в этой группе не найдено. Укажите другую заливку.')
        bins = (interior.astype(np.uint16)//4).astype(np.int32)
        codes = (bins[:,0]*64 + bins[:,1])*64 + bins[:,2]
        dominant = np.bincount(codes).argmax()
        selected = interior[codes == dominant]
        if len(selected) < 4:
            raise ValueError('Укажите участок с более однородным цветом.')
        representative = np.median(selected,axis=0)
    return dict(parent=parent, target=number, picked=picked.tolist(), base=representative.tolist())


def split_labels(lab, labels, sample, margin=None):
    result = labels.copy()
    mask = labels == sample['parent']
    colors = lab[mask]
    old = np.linalg.norm(colors-np.asarray(sample['base'],np.float32),axis=1)
    new = np.linalg.norm(colors-np.asarray(sample['picked'],np.float32),axis=1)
    result[mask] = np.where(new < old, sample['target'], sample['parent'])
    if margin is not None:
        margin[mask] = np.minimum(margin[mask],np.abs(new-old))
    return result


def sampled_preparation(prepared,samples,palette):
    if not samples:
        return prepared
    labels = prepared.assignment
    margin = prepared.margin.copy()
    centers = prepared.centers.copy()
    for sample in samples:
        labels = split_labels(prepared.lab,labels,sample,margin)
        centers[sample['parent']-1] = sample['base']
        centers = np.vstack((centers,np.asarray(sample['picked'],np.float32)))
    overview = sampled_preparation(prepared.overview,samples,palette) if prepared.overview else None
    return replace(prepared,sampled=True,assignment=labels,margin=margin,centers=centers,palette=palette,overview=overview)
