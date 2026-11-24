"""Atomic GIS export with independent polygon/line layers and physical measures."""
import os
from pathlib import Path
import tempfile

import fiona
from rasterio.crs import CRS
from shapely.geometry import mapping, MultiPolygon, MultiLineString

from .attributes import values_for
from .spatial import DISPLAY_CRS, project_geometry, measurements


def export_workspace(document, result, path, target=DISPLAY_CRS):
    path = Path(path)
    if path.exists():
        raise FileExistsError('Файл уже существует. Выберите новое имя.')
    target_crs = CRS.from_user_input(target)
    types = dict(text='str', integer='int64', number='float', boolean='int', date='date', choice='str')
    polygon_schema = dict(object_id='int64', class_id='int', class_name='str', note='str', review='str', origin='str',
                          source_sheet='str', source_group='int', area_m2='float', area_ha='float', perimeter_m='float')
    polygon_schema.update({f['key']: types[f['type']] for f in document.fields})
    line_schema = dict(object_id='int64', type_id='int', type_name='str', name='str', note='str', review='str', origin='str', length_m='float')
    line_schema.update({f['key']: types[f['type']] for f in document.line_fields})
    records = []
    for f in result.features:
        props = dict(f['properties'])
        props.update(values_for(document.fields, props, enforce_required=True))
        m = measurements(f['geometry'])
        props.update(class_name=document.classes[props['class_id']-1]['name'], area_m2=m['area_m2'], area_ha=m['area_m2']/10000, perimeter_m=m['length_m'])
        records.append(dict(geometry=mapping(project_geometry(f['geometry'], DISPLAY_CRS, target)),
                            properties={k: _value(props.get(k)) for k in polygon_schema}))
    line_records = []
    lookup = {t['id']: t['name'] for t in document.line_types}
    grouped = {}
    for f in document.clipped_lines():
        identity = f['properties']['object_id']
        grouped.setdefault(identity, dict(properties=f['properties'], parts=[]))['parts'].append(f['geometry'])
    for f in grouped.values():
        geometry = MultiLineString(f['parts'])
        props = dict(f['properties'])
        props.update(values_for(document.line_fields, props, enforce_required=True),
                     length_m=measurements(geometry)['length_m'], type_name=lookup[props['type_id']])
        line_records.append(dict(geometry=mapping(project_geometry(geometry, DISPLAY_CRS, target)),
                                 properties={k: _value(props.get(k)) for k in line_schema}))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.geotrace-export-', suffix='.gpkg', dir=path.parent)
    os.close(fd)
    os.unlink(temporary)
    try:
        for layer, geometry, schema, features in [('polygons', 'Polygon', polygon_schema, records),
                                                  ('lines', 'MultiLineString', line_schema, line_records)]:
            with fiona.open(temporary, 'w', driver='GPKG', layer=layer, crs_wkt=target_crs.to_wkt(),
                            schema=dict(geometry=geometry, properties=schema), encoding='UTF-8') as output:
                output.writerecords(features)
        if document.area is not None:
            geometry = project_geometry(document.area, DISPLAY_CRS, target)
            if geometry.geom_type == 'Polygon':
                geometry = MultiPolygon([geometry])
            with fiona.open(temporary, 'w', driver='GPKG', layer='licence_area', crs_wkt=target_crs.to_wkt(),
                            schema=dict(geometry='MultiPolygon', properties=dict(name='str', area_m2='float'))) as output:
                output.write(dict(geometry=mapping(geometry), properties=dict(name=document.areas[document.active_area]['name'],
                             area_m2=measurements(document.area)['area_m2'])))
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(path)


def _value(value):
    return int(value) if type(value) is bool else value
