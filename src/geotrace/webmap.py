"""Isolated, read-only Yandex web-map page for the desktop application."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from threading import RLock, Thread, BoundedSemaphore
from collections import OrderedDict
from io import BytesIO
import re
from html import escape

from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from socketserver import TCPServer
from urllib.parse import urlsplit

import numpy as np
from rasterio.warp import transform, transform_geom
from shapely.geometry import mapping


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer normally reverse-resolves the address while binding. A
        # loopback helper needs no DNS; a broken resolver must not freeze Qt.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address


def project_context(raster):
    if raster is None:
        return dict(name='', bounds=None, outline=[], areas=[])
    h, w = raster.valid.shape
    corners = [raster.transform*p for p in ((0, 0), (w, 0), (w, h), (0, h), (0, 0))]
    lon, lat = transform(raster.crs_wkt, 'EPSG:4326', *zip(*corners))
    points = np.column_stack((lat, lon))
    if not np.isfinite(points).all():
        raise ValueError('Не удалось определить положение листа.')
    areas = []
    if raster.area is not None:
        geometry = transform_geom(raster.crs_wkt, 'EPSG:4326', mapping(raster.area))
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        areas = [[[[float(y), float(x)] for x, y, *rest in ring] for ring in polygon]
                 for polygon in polygons]
    return dict(name=Path(raster.path).name, outline=points.tolist(), areas=areas,
                bounds=[points.min(axis=0).tolist(), points.max(axis=0).tolist()])


def vector_context(result, document=None):
    """Yandex GeoObject coordinates use latitude, longitude (including hole rings)."""
    features = []
    if result is not None:
        for feature in result.features:
            geometry = transform_geom(result.raster.crs_wkt, 'EPSG:4326', mapping(feature['geometry']))
            def swap(coordinates):
                if isinstance(coordinates[0], (int, float)):
                    return [float(coordinates[1]), float(coordinates[0])]
                return [swap(part) for part in coordinates]
            geometry['coordinates'] = swap(geometry['coordinates'])
            properties = feature['properties']
            rgb = result.colors[int(properties['class_id'])-1]
            cls=document.classes[int(properties['class_id'])-1] if document is not None else None
            style=cls['style'] if cls else dict(fill='#%02x%02x%02x'%tuple(rgb),outline='#184e55',width=1.4,opacity=1)
            features.append(dict(type='Feature', id=int(properties['object_id']), geometry=geometry,
                properties=dict(kind='polygon',baseFillOpacity=style['opacity'],hintContent=f"Полигон № {properties['object_id']} · "+escape(cls['name'] if cls else f"группа {properties['class_id']}")),
                options=dict(fillColor=style['fill'], strokeColor=style['outline'], strokeWidth=style['width'])))
        if document is not None:
            types={t['id']:t for t in document.line_types}
            for index,f in enumerate(document.clipped_lines()):
                t=types[f['properties']['type_id']];style=t['style']
                geometry=transform_geom('EPSG:3857','EPSG:4326',mapping(f['geometry']))
                geometry['coordinates']=[[float(y),float(x)] for x,y,*_ in geometry['coordinates']]
                features.append(dict(type='Feature',id=f'line:{f["properties"]["object_id"]}:{index}',geometry=geometry,
                    properties=dict(kind='line',hintContent=escape(t['name'])+f' · № {f["properties"]["object_id"]}'),
                    options=dict(strokeColor=style['color'],strokeWidth=style['width'],strokeStyle=style['dash'])))
    return dict(type='FeatureCollection', features=features)


class RasterTiles:
    """Bounded local RGBA tiles in Yandex's ellipsoidal Mercator (EPSG:3395)."""
    extent = np.pi*6378137

    def __init__(self, raster):
        self.transform, self.crs = raster.transform, raster.crs_wkt
        # Premultiply before resampling: NoData and KML holes cannot bleed dark RGB.
        alpha = raster.valid.astype('float32')
        self.source = np.concatenate((raster.rgb.astype('float32')*alpha[...,None],
                                      alpha[...,None]*255), axis=2).transpose(2,0,1)
        self.lock, self.slots = RLock(), BoundedSemaphore(2)
        self.cache = OrderedDict()

    def tile(self, z, x, y):
        if not 0 <= z <= 22 or not 0 <= y < 2**z:
            raise ValueError('Invalid tile')
        x %= 2**z
        key = (z,x,y)
        with self.slots:
            with self.lock:
                if key in self.cache:
                    self.cache.move_to_end(key)
                    return self.cache[key]
            span = 2*self.extent/(2**z)
            left, top = -self.extent+x*span, self.extent-y*span
            destination = np.zeros((4,256,256), dtype='float32')
            reproject(self.source, destination, src_transform=self.transform, src_crs=self.crs,
                      dst_transform=from_bounds(left, top-span, left+span, top, 256,256),
                      dst_crs='EPSG:3395', resampling=Resampling.bilinear, num_threads=1)
            alpha = destination[3]
            rgb = np.divide(destination[:3]*255, alpha[None],
                            out=np.zeros_like(destination[:3]), where=alpha[None]>0)
            rgba = np.concatenate((rgb,alpha[None])).clip(0,255).round().astype('uint8').transpose(1,2,0)
            buffer = BytesIO()
            Image.fromarray(rgba).save(buffer, format='PNG')
            data = buffer.getvalue()
            with self.lock:
                self.cache[key] = data
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
            return data


class WebMapServer:
    """Read-only snapshots and bounded raster tiles under a random loopback path."""
    def __init__(self, key):
        self.key = key
        self.context = dict(project_context(None), rasterRevision=0, vectorRevision=0,
                            rasterAvailable=False, polygonCount=0)
        self.vectors = vector_context(None)
        self.raster = self.result = self.tiles = None
        self.raster_revision = self.vector_revision = 0
        self.style_revision=None
        self.snapshot_lock = RLock()
        self.document = (Path(__file__).parent/'webmap'/'index.html').read_bytes()
        self.lock = RLock()
        self.token = secrets.token_urlsafe(24)
        self.server = None
        self.thread = None

    def update(self, key=None, context=None):
        with self.lock:
            if key is not None:
                self.key = key
            if context is not None:
                self.context = context

    def set_project(self, raster, result, document=None, style_revision=None):
        # Snapshots are rebuilt only when their source objects change, never on selection/status.
        with self.snapshot_lock:
            raster_changed = raster is not self.raster
            result_changed = result is not self.result or style_revision!=self.style_revision
            if not raster_changed and not result_changed:
                return
            context = project_context(raster)
            tiles = RasterTiles(raster) if raster_changed and raster is not None else self.tiles
            vectors = vector_context(result,document) if result_changed else self.vectors
            with self.lock:
                if raster_changed:
                    self.raster_revision += 1
                if result_changed:
                    self.vector_revision += 1
                context.update(rasterRevision=self.raster_revision, vectorRevision=self.vector_revision,
                               rasterAvailable=raster is not None, polygonCount=sum(f['properties'].get('kind')!='line' for f in vectors['features']),
                               lineCount=sum(f['properties'].get('kind')=='line' for f in vectors['features']))
                self.raster, self.result = raster, result
                self.style_revision=style_revision
                self.tiles = tiles if raster is not None else None
                self.vectors, self.context = vectors, context

    def start(self):
        if self.server is not None:
            return self.url
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlsplit(self.path).path
                prefix = '/'+owner.token+'/'
                if not path.startswith(prefix):
                    self.send_error(404)
                    return
                resource = path[len(prefix):]
                with owner.lock:
                    if resource == 'index.html':
                        data = owner.document
                        kind = 'text/html; charset=utf-8'
                    elif resource == 'config.json':
                        data = json.dumps(dict(apiKey=owner.key), ensure_ascii=False).encode('utf8')
                        kind = 'application/json; charset=utf-8'
                    elif resource == 'polygons.json':
                        data = json.dumps(dict(owner.vectors, revision=owner.vector_revision), ensure_ascii=False).encode('utf8')
                        kind = 'application/json; charset=utf-8'
                    elif resource == 'project.json':
                        data = json.dumps(owner.context, ensure_ascii=False).encode('utf8')
                        kind = 'application/json; charset=utf-8'
                    else:
                        match = re.fullmatch(r'raster/(\d{1,10})/(\d{1,2})/(-?\d{1,10})/(\d{1,10})\.png', resource)
                        tiles = owner.tiles
                        if not match or tiles is None or int(match[1]) != owner.raster_revision:
                            self.send_error(404)
                            return
                        data, kind = None, 'image/png'
                if data is None:
                    try:
                        data = tiles.tile(*map(int, match.groups()[1:]))
                    except (ValueError, OSError):
                        self.send_error(404)
                        return
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'private, max-age=86400' if kind == 'image/png' else 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Normal when a view is reloaded or closed mid-response.

            def log_message(self, *args):
                pass
        self.server = LoopbackServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/{self.token}/index.html'
        return self.url

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread.join(timeout=1)
