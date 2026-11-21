"""Temporary, bounded Yandex response cache; credentials never enter file names."""
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

MAX_AGE = 7*24*60*60
MAX_BYTES = 128*1024*1024


def tile_path(folder, key, zoom, x, y):
    scope = hashlib.sha256(key.encode('utf8')).hexdigest()
    return Path(folder)/'yandex-v1'/scope/f'tile-{zoom}-{x}-{y}.png'


def response_expiry(headers, now):
    controls = str(headers.get('Cache-Control', '')).lower().split(',')
    controls = [item.strip() for item in controls]
    if 'no-store' in controls or 'no-cache' in controls:
        return now
    expiry = now+MAX_AGE
    for item in controls:
        if item.startswith('max-age='):
            try:
                age = max(0., float(headers.get('Age', '0')))
                expiry = min(expiry, now+max(0., float(item[8:].strip('"'))-age))
            except (ValueError, TypeError):
                return now
    if headers.get('Expires'):
        try:
            expiry = min(expiry, parsedate_to_datetime(headers['Expires']).timestamp())
        except (ValueError, TypeError, OverflowError):
            return now
    return expiry if math.isfinite(expiry) else now


def discard(path):
    for entry in (path, path.with_suffix('.json')):
        try:
            entry.unlink(missing_ok=True)
        except OSError:
            pass


def cached_expiry(path, now):
    try:
        data = json.loads(path.with_suffix('.json').read_text())
        created, expires = float(data['created']), float(data['expires'])
        if not (math.isfinite(created) and math.isfinite(expires) and
                0 <= created <= now < expires <= created+MAX_AGE):
            return None
        return expires
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store_tile(path, data, expires, now):
    if expires <= now:
        discard(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temp = path.with_suffix('.'+token+'.tmp')
    meta = path.with_suffix('.'+token+'.json.tmp')
    try:
        temp.write_bytes(data)
        meta.write_text(json.dumps(dict(created=now, expires=expires)))
        os.replace(temp, path)
        os.replace(meta, path.with_suffix('.json'))
    finally:
        temp.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)


def prune_yandex_cache(folder, limit=MAX_BYTES, now=None):
    now = time.time() if now is None else now
    entries = []
    for path in (Path(folder)/'yandex-v1').glob('*/tile-*.png'):
        try:
            if cached_expiry(path, now) is None:
                discard(path)
                continue
            entries.append((path.stat().st_mtime, path.stat().st_size+path.with_suffix('.json').stat().st_size, path))
        except OSError:
            continue
    size = sum(item[1] for item in entries)
    for _, length, path in sorted(entries):
        if size <= limit:
            break
        discard(path)
        size -= length
