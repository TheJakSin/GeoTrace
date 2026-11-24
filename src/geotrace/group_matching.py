"""Pick native sheet groups without covering the map with a comparison table."""
from pathlib import Path
import math

import cv2
import numpy as np
from PIL import Image
from rasterio.warp import reproject,Resampling
from shapely.geometry import Point

from .spatial import DISPLAY_CRS,project_geometry


def group_at(document,x,y,sheet_id=''):
    sheets=[document.sheet(sheet_id)] if sheet_id else list(reversed(document.sheets))
    for sheet in sheets:
        if sheet is None or sheet.result is None or (not sheet_id and not sheet.visible):continue
        point=project_geometry(Point(x,y),DISPLAY_CRS,sheet.raster.crs_wkt)
        col,row=(~sheet.raster.transform)*(point.x,point.y)
        col,row=math.floor(col),math.floor(row)
        h,w=sheet.result.labels.shape
        if not 0<=col<w or not 0<=row<h or not sheet.raster.valid[row,col]:continue
        group=int(sheet.result.labels[row,col])
        if group:return group_record(document,sheet.id,group)
    return None


def group_record(document,sheet_id,group=0):
    sheet=document.sheet(sheet_id)
    if sheet is None or sheet.result is None:return {}
    if group and not 1<=group<=len(sheet.result.colors):raise ValueError('Группа листа не найдена.')
    cid=sheet.groups.get(group,0)
    return dict(sheet=sheet.id,sheetName=sheet.name,group=group,classId=cid,
        className=document.classes[cid-1]['name'] if cid else '',
        color='#%02x%02x%02x'%tuple(sheet.result.colors[group-1]) if group else '#eef1f6')


def paint_groups(document,raster,first,second,path):
    rgba=np.zeros((*raster.valid.shape,4),np.uint8)
    for record,color in ((first,(53,106,195)),(second,(215,129,25))):
        if not record.get('group'):continue
        sheet=document.sheet(record['sheet'])
        if sheet is None or sheet.result is None:continue
        mask=np.zeros(raster.valid.shape,np.uint8)
        reproject((sheet.result.labels==record['group']).astype('uint8'),mask,
            src_transform=sheet.raster.transform,src_crs=sheet.raster.crs_wkt,
            dst_transform=raster.transform,dst_crs=DISPLAY_CRS,resampling=Resampling.nearest,num_threads=1)
        mask=mask.astype(bool)&raster.valid
        border=cv2.morphologyEx(mask.astype('uint8'),cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8)).astype(bool)&raster.valid
        rgba[mask,:3]=color;rgba[mask,3]=55;rgba[border,:3]=color;rgba[border,3]=230
    Image.fromarray(rgba).save(Path(path))
