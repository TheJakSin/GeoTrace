"""One cartographic representation shared by preview, SVG and vector PDF export."""
from base64 import b64encode
from html import escape
from io import BytesIO
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from shapely.geometry import box

from .document import line_parts
from .region import polygon_parts
from .spatial import DISPLAY_CRS, local_scale, measurements


def preview_overlay(document, result, path, lines_path=None):
    raster = result.raster
    w, h = raster.valid.shape[1], raster.valid.shape[0]
    # One rasterisation avoids allocating/compositing a full-page image for each
    # polygon. Thousands of small polygons then cost pixels + boundary vertices.
    lookup=np.zeros((len(document.classes)+1,4),np.uint8)
    for cls in document.classes:
        style=cls['style']
        lookup[cls['id'],:3]=list(bytes.fromhex(style['fill'][1:]))
        lookup[cls['id'],3]=round(255*style['opacity'])
    labels=rasterize([(f['geometry'],f['properties']['class_id']) for f in result.features],
                     out_shape=(h,w),transform=raster.transform,dtype='uint16') if result.features else np.zeros((h,w),np.uint16)
    labels[~raster.valid]=0
    image = Image.fromarray(lookup[labels])
    inverse = ~raster.transform
    draw=ImageDraw.Draw(image)
    for feature in result.features:
        cls = document.classes[feature['properties']['class_id']-1]
        style = cls['style']
        for part in polygon_parts(feature['geometry']):
            for ring in [part.exterior, *part.interiors]:
                draw.line([inverse*p for p in ring.coords], fill=style['outline'], width=max(1, round(style['width'])))
    image.save(path)
    if lines_path is not None:
        lines = Image.new('RGBA', (w, h))
        draw = ImageDraw.Draw(lines)
        types = {t['id']: t for t in document.line_types}
        for feature in document.clipped_lines():
            item = types.get(feature['properties']['type_id'])
            if item is None:
                continue
            style = item['style']
            points = [inverse*p for p in feature['geometry'].coords]
            if style['dash'] == 'solid':
                draw.line(points, fill=style['color'], width=max(1, round(style['width'])))
            else:
                _dashed(draw, points, style)
        lines.save(lines_path)


def _dashed(draw, points, style):
    period, on = (9, 5) if style['dash'] == 'dash' else (5, 1.5)
    distance = 0.
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        position = 0.
        while position < length:
            phase = distance % period
            step = min(length-position, (on-phase) if phase < on else (period-phase))
            if step < 1e-8:
                step = min(.01, length-position)
            if phase < on:
                p = [(a[0]+(b[0]-a[0])*v/length, a[1]+(b[1]-a[1])*v/length) for v in (position, position+step)]
                draw.line(p, fill=style['color'], width=max(1, round(style['width'])))
            position += step
            distance += step


def map_image(document, bounds, width, height, detailed=False, only_sheet=None):
    if width*height > 24_000_000 or min(width, height) < 1:
        raise ValueError('Размер растрового вывода превышает 24 млн пикселей.')
    affine = from_bounds(*bounds, width, height)
    rgb = np.full((height, width, 3), 255, np.uint8)
    alpha = np.zeros((height, width), np.uint8)
    sheets = document.sheets if only_sheet is None else [document.sheet(only_sheet)]
    for sheet in sheets:
        if sheet is None or (only_sheet is None and not sheet.visible):
            continue
        raster = sheet.raster
        if detailed:
            from .detail import source_window
            raster = source_window(raster, bounds, side=min(6000, max(width, height)*2))
            if raster is None:
                continue
        mask = np.zeros((height, width), np.uint8)
        reproject(raster.valid.astype(np.uint8)*255, mask, src_transform=raster.transform, src_crs=raster.crs_wkt,
                  dst_transform=affine, dst_crs=DISPLAY_CRS, resampling=Resampling.nearest, num_threads=1)
        weights=raster.valid.astype('float32')
        coverage=np.zeros((height,width),'float32')
        reproject(weights,coverage,src_transform=raster.transform,src_crs=raster.crs_wkt,
                  dst_transform=affine,dst_crs=DISPLAY_CRS,resampling=Resampling.bilinear,num_threads=1)
        for channel in range(3):
            band = np.zeros((height, width), 'float32')
            reproject(raster.rgb[:, :, channel].astype('float32')*weights, band, src_transform=raster.transform, src_crs=raster.crs_wkt,
                      dst_transform=affine, dst_crs=DISPLAY_CRS, resampling=Resampling.bilinear, num_threads=1)
            np.divide(band,coverage,out=band,where=coverage>0)
            rgb[:, :, channel][mask > 0] = band[mask > 0].clip(0,255).round().astype('uint8')
        alpha[mask > 0] = 255
    if document.area is not None:
        alpha *= rasterize([(document.area, 1)], out_shape=(height, width), transform=affine, dtype='uint8')
    return Image.fromarray(np.dstack((rgb, alpha)))


def layout_defaults(document):
    return dict(name='Карта участка', title='Геологическая карта', subtitle='', paper='A3', landscape=True,
                dpi=300, raster=True, polygons=True, lines=True, grid=False, north=True, scalebar=True,
                legend=True, legend_field='class_name', labels='', class_ids=[c['id'] for c in document.classes],
                extent='area', bounds=None, scale=0, footer='', legend_names={}, positions={
                    'map':[.035,.12,.70,.77], 'title':[.035,.02,.93,.085],
                    'legend':[.76,.12,.205,.77], 'north':[.68,.145,.04,.065],
                    'scalebar':[.53,.83,.18,.055], 'footer':[.035,.92,.93,.065]})


def validate_layout(settings, document):
    if not isinstance(settings, dict):
        raise ValueError('Некорректный макет.')
    result = layout_defaults(document)
    for key, value in settings.items():
        if key not in result:
            raise ValueError('Неизвестный параметр макета.')
        result[key] = value
    if result['paper'] not in ('A4', 'A3', 'A2', 'A1', 'A0') or type(result['landscape']) is not bool:
        raise ValueError('Выберите формат листа.')
    if type(result['dpi']) is not int or not 72 <= result['dpi'] <= 600:
        raise ValueError('Разрешение: от 72 до 600 DPI.')
    for key in ('raster', 'polygons', 'lines', 'grid', 'north', 'scalebar', 'legend'):
        if type(result[key]) is not bool:
            raise ValueError('Некорректная видимость элемента макета.')
    for key in ('name', 'title', 'subtitle', 'footer', 'labels', 'legend_field'):
        if not isinstance(result[key], str) or len(result[key]) > 400:
            raise ValueError('Слишком длинная подпись макета.')
    if result['extent'] not in ('area', 'view', 'bounds'):
        raise ValueError('Неизвестная область макета.')
    if result['bounds'] is not None:
        b = result['bounds']
        if not isinstance(b, list) or len(b) != 4 or not np.isfinite(b).all() or b[0] >= b[2] or b[1] >= b[3]:
            raise ValueError('Некорректная область вывода.')
    if type(result['scale']) not in (int, float) or not 0 <= result['scale'] <= 100_000_000:
        raise ValueError('Некорректный масштаб.')
    if not isinstance(result['class_ids'], list) or any(type(c) is not int or not 1 <= c <= len(document.classes) for c in result['class_ids']):
        raise ValueError('Некорректные классы легенды.')
    if not isinstance(result['legend_names'], dict) or any(not isinstance(v, str) or len(v) > 200 for v in result['legend_names'].values()):
        raise ValueError('Некорректные названия легенды.')
    if len(result['class_ids']) != len(set(result['class_ids'])):
        raise ValueError('Классы легенды не должны повторяться.')
    if not isinstance(result['positions'], dict) or set(result['positions']) != {'map','title','legend','north','scalebar','footer'}:
        raise ValueError('Некорректный состав элементов макета.')
    for rect in result['positions'].values():
        if (not isinstance(rect,list) or len(rect)!=4 or not np.isfinite(rect).all() or
                min(rect[:2])<0 or min(rect[2:])<.02 or rect[0]+rect[2]>1.00001 or rect[1]+rect[3]>1.00001):
            raise ValueError('Элементы макета должны находиться на странице.')
    return result


def layout_svg(document, result, settings, preview=False):
    """Return pages: a vector map and continuation pages for an oversized legend."""
    settings = validate_layout(settings, document)
    sizes = dict(A4=(210, 297), A3=(297, 420), A2=(420, 594), A1=(594, 841), A0=(841, 1189))
    width, height = sizes[settings['paper']]
    if settings['landscape']:
        width, height = height, width
    width, height = width*72/25.4, height*72/25.4
    def element(name):
        x,y,w,h=settings['positions'][name]
        return x*width,y*height,w*width,h*height
    margin, top, mw, mh = element('map')
    legend_x, legend_y, legend_w, legend_h = element('legend')
    bounds = settings['bounds'] or ((document.area.bounds if document.area is not None else None) or result.raster.area.bounds)
    cx, cy = (bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2
    ground = local_scale(box(*bounds))
    if settings['scale']:
        span_x = mw/72*.0254*settings['scale']/ground
        span_y = mh/72*.0254*settings['scale']/ground
    else:
        factor = min(mw/(bounds[2]-bounds[0]), mh/(bounds[3]-bounds[1]))*.96
        span_x, span_y = mw/factor, mh/factor
    bounds = (cx-span_x/2, cy-span_y/2, cx+span_x/2, cy+span_y/2)
    k = mw/span_x
    def xy(x, y):
        return margin+(x-bounds[0])*k, top+(bounds[3]-y)*k
    def path(geometry):
        parts = []
        rings = [geometry.exterior, *geometry.interiors] if geometry.geom_type == 'Polygon' else [geometry]
        for ring in rings:
            pts = [xy(*p) for p in ring.coords]
            parts.append('M'+' L'.join(f'{x:.4f},{y:.4f}' for x, y in pts)+(' Z' if geometry.geom_type == 'Polygon' else ''))
        return ' '.join(parts)
    def text(x, y, content, size=10, color='#263445', weight='normal'):
        return f'<text x="{x:.3f}" y="{y:.3f}" font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(str(content))}</text>'
    head = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>'
    tx,ty,tw,th=element('title')
    content = [head, text(tx, ty+19, settings['title'], 18, weight='bold'), text(tx, ty+36, settings['subtitle'], 10)]
    content.append(f'<defs><clipPath id="map"><rect x="{margin}" y="{top}" width="{mw}" height="{mh}"/></clipPath></defs><g clip-path="url(#map)">')
    dpi = min(110, settings['dpi']) if preview else settings['dpi']
    if settings['raster']:
        iw, ih = max(1, round(mw/72*dpi)), max(1, round(mh/72*dpi))
        if iw*ih > 24_000_000:
            ratio = math.sqrt(24_000_000/(iw*ih))
            iw, ih = max(1, int(iw*ratio)), max(1, int(ih*ratio))
        image = map_image(document, bounds, iw, ih, detailed=not preview)
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        content.append(f'<image x="{margin}" y="{top}" width="{mw}" height="{mh}" preserveAspectRatio="none" xlink:href="data:image/png;base64,{b64encode(buffer.getvalue()).decode()}"/>')
    visible = box(*bounds)
    if settings['polygons']:
        for f in result.features:
            if not f['geometry'].intersects(visible):
                continue
            style = document.classes[f['properties']['class_id']-1]['style']
            for p in polygon_parts(f['geometry'].intersection(visible)):
                content.append(f'<path d="{path(p)}" fill="{style["fill"]}" fill-opacity="{style["opacity"]}" stroke="{style["outline"]}" stroke-width="{style["width"]*.5}" fill-rule="evenodd"/>')
    if settings['lines']:
        types = {t['id']: t for t in document.line_types}
        for f in document.clipped_lines():
            if not f['geometry'].intersects(visible):
                continue
            style = types[f['properties']['type_id']]['style']
            dash = {'solid': '', 'dash': 'stroke-dasharray="5 3"', 'dot': 'stroke-dasharray="1 3"'}[style['dash']]
            for line in line_parts(f['geometry'].intersection(visible)):
                content.append(f'<path d="{path(line)}" fill="none" stroke="{style["color"]}" stroke-width="{style["width"]*.6}" {dash}/>')
    # Suppress colliding labels rather than printing an unreadable pile of text.
    placed = []
    field = settings['labels']
    if field or any(c['style'].get('label') for c in document.classes):
        for f in sorted(result.features, key=lambda f: -f['geometry'].area):
            point = f['geometry'].intersection(visible).representative_point()
            if point.is_empty:
                continue
            cls = document.classes[f['properties']['class_id']-1]
            key = field or cls['style'].get('label','')
            value = cls['name'] if key == 'class_name' else f['properties'].get(key)
            if value is None:
                continue
            x, y = xy(point.x, point.y)
            label = str(value)[:60]
            rect = box(x-2, y-9, x+len(label)*5.5+2, y+2)
            if any(rect.intersects(p) for p in placed):
                continue
            placed.append(rect)
            content.append(text(x, y, label, 9))
    if settings['grid']:
        for i in range(1, 5):
            x, y = margin+mw*i/5, top+mh*i/5
            content.extend([f'<path d="M{x},{top}v{mh} M{margin},{y}h{mw}" stroke="#778899" stroke-opacity=".4" stroke-width=".4"/>',
                            text(x+2, top+12, f'{bounds[0]+span_x*i/5:.0f}', 7),
                            text(margin+2, y-2, f'{bounds[3]-span_y*i/5:.0f}', 7)])
    content.append('</g>')
    content.append(f'<rect x="{margin}" y="{top}" width="{mw}" height="{mh}" fill="none" stroke="#718091" stroke-width=".6"/>')
    scale = ground/k/(.0254/72)
    fx,fy,fw,fh=element('footer')
    content.append(text(fx, fy+10, f'1:{round(scale):,} · EPSG:3857'.replace(',', ' '), 9))
    content.append(text(fx, fy+24, settings['footer'], 8))
    if settings['scalebar']:
        sx,sy,sw,sh=element('scalebar')
        target = sw*.8/k*ground
        order = 10**math.floor(math.log10(max(target, .001)))
        metres = next(v*order for v in (1, 2, 5, 10) if v*order >= target*.55)
        length = metres/ground*k
        x, y = sx, sy+sh*.65
        for i in range(4):
            content.append(f'<rect x="{x+i*length/4}" y="{y-3}" width="{length/4}" height="6" fill="'+('black' if i%2==0 else 'white')+'" stroke="black" stroke-width=".6"/>')
        content.append(text(x,y+14,'0',7))
        content.append(text(x+length/2-8,y+14,f'{metres/2000:g}' if metres>=1000 else f'{metres/2:g}',7))
        content.append(text(x+length-8,y+14,f'{metres/1000:g} км' if metres>=1000 else f'{metres:g} м',7))
    if settings['north']:
        nx,ny,nw,nh=element('north')
        x, y = nx+nw/2, ny+13
        content.append(f'<path d="M{x},{y-12}l-5,17 5,-4 5,4z" fill="#263445"/>')
        content.append(text(x-3, y+16, 'N', 9))
    entries = []
    for cid in settings['class_ids']:
        cls = document.classes[cid-1]
        if settings['legend_field'] == 'class_name':
            label = cls['name']
        else:
            values = sorted({str(f['properties'].get(settings['legend_field'], '')) for f in result.features if f['properties']['class_id'] == cid})
            label = ' / '.join(v for v in values if v) or cls['name']
        label = settings['legend_names'].get(str(cid), label)
        entries.append((cls['style']['fill'], label, False))
    if settings['lines']:
        entries.extend((t['style']['color'], t['name'], True) for t in document.line_types)
    pages = []
    if settings['legend']:
        body=list(content)
        lx,ly,lw,lh=legend_x,legend_y,legend_w,legend_h
        body.append(text(lx,ly+10,'Условные обозначения',11,weight='bold'))
        y=ly+30
        for color,label,line in entries:
            chars=max(8,int((lw-25)/4.4))
            chunks=[label[k:k+chars] for k in range(0,len(label),chars)] or ['']
            for j,chunk in enumerate(chunks):
                if y+12>ly+lh:
                    pages.append(''.join(body)+'</svg>')
                    lx,ly,lw,lh=width*.04,height*.12,width*.92,height*.8
                    body=[head,text(width*.04,30,settings['title']+' · легенда',16),text(lx,ly+10,'Условные обозначения · продолжение',11,weight='bold')]
                    y=ly+30
                if j==0:
                    body.append(f'<path d="M{lx},{y}h18" stroke="{color}" stroke-width="2"/>' if line else f'<rect x="{lx}" y="{y-9}" width="18" height="11" fill="{color}" stroke="#687383" stroke-width=".4"/>')
                body.append(text(lx+24,y,chunk,8))
                y+=10
            y+=10
        pages.append(''.join(body)+'</svg>')
    else:
        pages.append(''.join(content)+'</svg>')
    return pages


def write_layout(document, result, settings, path, atlas=False):
    from PySide6.QtCore import QByteArray, QRectF, QSizeF
    from PySide6.QtGui import QImage, QPainter, QPdfWriter, QPageSize
    from PySide6.QtSvg import QSvgRenderer
    path = Path(path)
    if path.exists():
        raise FileExistsError('Файл уже существует. Выберите новое имя.')
    if atlas and path.suffix.lower()!='.pdf':
        raise ValueError('Атлас сохраняется в многостраничный PDF.')
    if atlas:
        from shapely.ops import unary_union
        areas = document.areas
        if len(areas)>1 and areas[0]['geometry'].equals(unary_union([a['geometry'] for a in areas[1:]])):
            areas = areas[1:]
        if not areas:
            raise ValueError('Для атласа добавьте участки KML.')
        pages=[]
        for area in areas:
            doc=document.clone()
            doc.active_area=next(i for i,a in enumerate(doc.areas) if a['name']==area['name'] and a['geometry'].equals(area['geometry']))
            try:
                output=doc.compose(doc.canvas())
            except ValueError as exc:
                raise ValueError(f'Участок «{area["name"]}»: {exc}') from exc
            options=dict(settings,title=settings['title']+' · '+area['name'],extent='area',bounds=None)
            pages.extend(layout_svg(doc,output,options))
    else:
        pages = layout_svg(document, result, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.geotrace-layout-', suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        if path.suffix.lower() == '.svg':
            if len(pages) > 1:
                raise ValueError('Для многостраничной легенды выберите PDF.')
            Path(temporary).write_text(pages[0], encoding='utf-8')
        elif path.suffix.lower() == '.pdf':
            renderer = QSvgRenderer(QByteArray(pages[0].encode()))
            size = renderer.viewBoxF().size()
            writer = QPdfWriter(temporary)
            writer.setResolution(144)
            writer.setPageSize(QPageSize(QSizeF(size.width()*25.4/72, size.height()*25.4/72), QPageSize.Millimeter))
            writer.setPageMargins(__import__('PySide6.QtCore', fromlist=['QMarginsF']).QMarginsF(0, 0, 0, 0))
            writer.setTitle(settings['title'])
            painter = QPainter(writer)
            for i, source in enumerate(pages):
                if i:
                    writer.newPage()
                QSvgRenderer(QByteArray(source.encode())).render(painter, QRectF(0, 0, writer.width(), writer.height()))
            painter.end()
            del writer
        elif path.suffix.lower() == '.png':
            if len(pages) > 1:
                raise ValueError('Для многостраничной легенды выберите PDF.')
            renderer = QSvgRenderer(QByteArray(pages[0].encode()))
            size = renderer.viewBoxF().size()
            w, h = round(size.width()/72*settings['dpi']), round(size.height()/72*settings['dpi'])
            if w*h > 100_000_000:
                raise ValueError('Уменьшите DPI или выберите PDF: PNG превышает 100 млн пикселей.')
            image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            image.fill(0xffffffff)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            if not image.save(temporary, 'PNG'):
                raise ValueError('Не удалось сохранить изображение макета.')
        else:
            raise ValueError('Форматы макета: PDF, PNG или SVG.')
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(path)
