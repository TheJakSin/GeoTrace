"""Project groups are semantic classes; native recognition labels stay intact."""
import numpy as np

from .core import Result
from .styles import validate_style


def palette(document):
    return [list(bytes.fromhex(c['style']['fill'][1:])) for c in document.classes]


def create_group(document, name, color='#80b5d2'):
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError('Укажите название группы, не более 200 символов.')
    if len(document.classes) >= 65534:
        raise ValueError('Достигнут предел групп.')
    cid = len(document.classes) + 1
    document.classes.append(dict(id=cid, name=name.strip(), style=validate_style(dict(fill=color))))
    if document.manual is not None:
        r = document.manual
        document.manual = Result(r.raster, r.labels, r.features, palette(document), r.fields)
    return cid


def replace_groups(document, removed, target):
    """Transfer every reference, remove duplicates and compact all group indices."""
    known = {c['id'] for c in document.classes}
    removed = set(removed) - {target}
    if not removed or not removed <= known or (target and target not in known):
        raise ValueError('Выберите разные существующие группы.')
    kept = [c for c in document.classes if c['id'] not in removed]
    remap = {c['id']: i for i, c in enumerate(kept, 1)}
    remap.update({cid: remap.get(target,0) for cid in removed})
    document.classes = [dict(c, id=i) for i, c in enumerate(kept, 1)]
    for sheet in document.sheets:
        sheet.groups = {local: remap.get(cid,0) for local, cid in sheet.groups.items()}
    unassigned={f'{sheet.id}:{local}' for sheet in document.sheets for local,cid in sheet.groups.items() if not cid}
    document.settings['matched_groups']=[key for key in document.settings.get('matched_groups',[]) if key not in unassigned]
    if document.manual is not None:
        r = document.manual
        lut = np.zeros(max(known) + 1, np.uint16)
        for before, after in remap.items():
            lut[before] = after
        features = [dict(geometry=f['geometry'], properties=dict(f['properties'], class_id=remap[f['properties']['class_id']])) for f in r.features if remap[f['properties']['class_id']]]
        document.manual = Result(r.raster, lut[r.labels], features, palette(document), r.fields)
    for layout in document.layouts:
        if 'class_ids' in layout:
            layout['class_ids'] = list(dict.fromkeys(remap[cid] for cid in layout['class_ids'] if remap.get(cid)))
        names = layout.get('legend_names', {})
        layout['legend_names'] = {str(remap[int(cid)]): value for cid, value in names.items() if int(cid) in remap and int(cid) not in removed}
    hidden = document.settings.get('hidden_classes', [])
    document.settings['hidden_classes'] = sorted({remap[cid] for cid in hidden if cid in remap and cid not in removed})
    if document.settings.get('draw_class') in remap:
        document.settings['draw_class'] = remap[document.settings['draw_class']]
    return remap.get(target,0)


def remove_group(document, identity, target=0):
    if identity not in {c['id'] for c in document.classes}:
        raise ValueError('Группа не найдена.')
    used = document.manual is not None and any(f['properties']['class_id']==identity for f in document.manual.features)
    if document.manual is None:
        used = any(s.result is not None and any(s.groups.get(f['properties']['class_id'])==identity for f in s.result.features) for s in document.sheets)
    if used and not target:
        raise ValueError('Выберите группу для переноса объектов перед удалением.')
    # A native colour may still exist outside KML. Zero means unassigned, not a
    # transfer into an unrelated class. Its original palette stays untouched.
    return replace_groups(document,[identity],target if used else 0)


def match_groups(document, records, target=0, name=''):
    sources = {document.sheet(r['sheet']).groups.get(r['group'],0) for r in records}
    sources.discard(0)
    if name.strip() or not target:
        sheet=document.sheet(records[0]['sheet']);local=records[0]['group'];cid=sheet.groups.get(local,0)
        first=document.classes[cid-1] if cid else dict(name=f'Группа {local}',style=dict(fill='#%02x%02x%02x'%tuple(sheet.result.colors[local-1])))
        target = create_group(document, name.strip() or first['name'], first['style']['fill'])
    if target not in {c['id'] for c in document.classes}:
        raise ValueError('Общая группа не найдена.')
    done = set(document.settings.get('matched_groups', []))
    for sheet in document.sheets:
        done.update(f'{sheet.id}:{local}' for local, cid in sheet.groups.items() if cid in sources)
    for r in records:
        document.sheet(r['sheet']).groups[r['group']]=target
        done.add(f"{r['sheet']}:{r['group']}")
    document.settings['matched_groups'] = sorted(done)
    removed = sources - {target}
    return replace_groups(document, removed, target) if removed else target


def visible_features(document, features):
    hidden = set(document.settings.get('hidden_classes', []))
    return [f for f in features if f['properties']['class_id'] not in hidden]
