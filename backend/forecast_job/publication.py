"""Immutable synthetic runs and compare-and-swap promotion of a complete manifest."""

from datetime import datetime, timezone
import hashlib
import json
import math
import re

from forecast_job.storage import Conflict


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('UTC timestamps required')
    return parsed


def validate(payload):
    if payload['schema_version'] != '1.0' or payload['source'] != 'sample':
        raise ValueError('Only schema 1.0 synthetic fixtures are accepted')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', payload['run_id']):
        raise ValueError('Invalid run ID')
    generated, start, end = map(timestamp, (payload['generated_at'], payload['valid_from'], payload['valid_until']))
    if not start < end or not generated < end:
        raise ValueError('Invalid validity interval')
    bounds = payload['bounds']
    if not -90 <= bounds['min_lat'] < bounds['max_lat'] <= 90 or not -180 <= bounds['min_lon'] < bounds['max_lon'] <= 180:
        raise ValueError('Invalid bounds')
    if payload['variables']['sample_index']['unit'] != 'dimensionless':
        raise ValueError('Invalid units')
    previous = None
    if not payload['forecasts']:
        raise ValueError('Empty forecast')
    for frame in payload['forecasts']:
        valid = timestamp(frame['valid_at'])
        if not start <= valid < end or (previous is not None and valid <= previous):
            raise ValueError('Invalid or unordered forecast time')
        previous = valid
        if not frame['cells']:
            raise ValueError('Empty cells')
        for cell in frame['cells']:
            if not bounds['min_lat'] <= cell['lat'] <= bounds['max_lat'] or not bounds['min_lon'] <= cell['lon'] <= bounds['max_lon']:
                raise ValueError('Cell outside coverage')
            if isinstance(cell['sample_index'], bool) or not math.isfinite(cell['sample_index']):
                raise ValueError('Invalid synthetic value')
    encode(payload)
    return payload


def read_run(store, run_id, manifest_generation=None):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', run_id):
        raise ValueError('Invalid run ID')
    manifest_obj = store.read(f'runs/{run_id}/manifest.json', manifest_generation)
    manifest = json.loads(manifest_obj.data)
    expected_path = f'runs/{run_id}/forecast.json'
    if manifest['schema_version'] != '1.0' or manifest['run_id'] != run_id or manifest['path'] != expected_path:
        raise ValueError('Invalid manifest')
    obj = store.read(expected_path, manifest['generation'])
    if len(obj.data) != manifest['bytes'] or hashlib.sha256(obj.data).hexdigest() != manifest['sha256']:
        raise ValueError('Forecast integrity check failed')
    payload = validate(json.loads(obj.data))
    if payload['run_id'] != run_id:
        raise ValueError('Run mismatch')
    return payload, manifest_obj


def read_current(store):
    pointer_obj = store.read('current.json')
    pointer = json.loads(pointer_obj.data)
    if pointer['schema_version'] != '1.0':
        raise ValueError('Unsupported pointer version')
    payload, _ = read_run(store, pointer['run_id'], pointer['manifest_generation'])
    if payload['generated_at'] != pointer['generated_at']:
        raise ValueError('Pointer timestamp mismatch')
    return payload, pointer_obj


def restore_run(store, run_id):
    """Explicit operator rollback; a concurrent promotion causes failure, not retry."""
    payload, manifest = read_run(store, run_id)
    current = store.read('current.json')
    pointer = encode({'schema_version': '1.0', 'run_id': run_id,
                      'generated_at': payload['generated_at'],
                      'manifest_generation': manifest.generation})
    store.write('current.json', pointer, expected=current.generation)
    return payload


def publish(store, payload, fail_before_promotion=False, now=None):
    validate(payload)
    now = now or datetime.now(timezone.utc)
    if not timestamp(payload['valid_from']) <= now < timestamp(payload['valid_until']):
        raise ValueError('Cannot promote an expired or future-only fixture')
    run_id = payload['run_id']
    data = encode(payload)
    path = f'runs/{run_id}/forecast.json'
    # Duplicate delivery is acceptable only when the immutable contents agree.
    try:
        obj = store.write(path, data)
    except Conflict:
        obj = store.read(path)
        if obj.data != data:
            raise ValueError('Run ID reused for different contents')
    manifest_data = encode({'schema_version': '1.0', 'run_id': run_id, 'path': path,
                            'generation': obj.generation, 'bytes': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()})
    manifest_path = f'runs/{run_id}/manifest.json'
    try:
        manifest = store.write(manifest_path, manifest_data)
    except Conflict:
        manifest = store.read(manifest_path)
        if manifest.data != manifest_data:
            raise ValueError('Manifest conflict')
    # Independent readback before exposing a run, also handles ambiguous writes.
    if store.read(path, obj.generation).data != data or store.read(manifest_path, manifest.generation).data != manifest_data:
        raise ValueError('Upload readback mismatch')
    if fail_before_promotion:
        raise RuntimeError('Controlled publication failure before pointer promotion')
    candidate_order = (timestamp(payload['generated_at']), run_id)
    for _ in range(5):
        try:
            current, current_obj = read_current(store)
        except KeyError:
            # Only absent pointer means a first publication; broken referenced runs
            # must not be mistaken for an empty bucket.
            try:
                store.read('current.json')
            except KeyError:
                current, current_obj = None, None
            else:
                raise ValueError('Current run references missing objects')
        if current and candidate_order <= (timestamp(current['generated_at']), current['run_id']):
            return False
        pointer = encode({'schema_version': '1.0', 'run_id': run_id,
                          'generated_at': payload['generated_at'],
                          'manifest_generation': manifest.generation})
        try:
            store.write('current.json', pointer, expected=current_obj.generation if current_obj else 0)
            return True
        except Conflict:
            continue
    raise Conflict('Concurrent promotion limit reached; current run preserved')


def available_forecast(store, lat, lon, now=None):
    payload, _ = read_current(store)
    now = now or datetime.now(timezone.utc)
    if not timestamp(payload['valid_from']) <= now < timestamp(payload['valid_until']):
        raise ValueError('Forecast is expired or not yet valid')
    bounds = payload['bounds']
    if not bounds['min_lat'] <= lat <= bounds['max_lat'] or not bounds['min_lon'] <= lon <= bounds['max_lon']:
        raise LookupError('Outside synthetic coverage')
    return payload


def verify_freshness(payload, now=None, max_age_hours=26):
    """Operational refresh alarm, independent of the fixture's 48-hour horizon."""
    now = now or datetime.now(timezone.utc)
    if not timestamp(payload['valid_from']) <= now < timestamp(payload['valid_until']):
        raise ValueError('Current synthetic forecast is not valid now')
    age = (now - timestamp(payload['generated_at'])).total_seconds()
    if age > max_age_hours * 3600 or age < -300:
        raise ValueError('Publication is overdue or its generation clock is invalid')
    return age
