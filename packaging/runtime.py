"""Locate bundled GDAL resources before either native GIS binding imports."""
import os
from pathlib import Path
import sys

root=Path(sys._MEIPASS)
data=root/'rasterio/gdal_data'
if data.is_dir():
    os.environ['GDAL_DATA']=str(data)
