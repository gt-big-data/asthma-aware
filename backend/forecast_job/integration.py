"""GCS failure, concurrency, immutability, and pointer-recovery acceptance drills.

Run only against the isolated development bucket, as its publisher identity.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import sys
from uuid import uuid4

from forecast_job.publication import encode, publish, read_current, read_run, restore_run
from forecast_job.sample import generate_sample_forecast
from forecast_job.storage import GCSStore


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    from google.api_core.exceptions import Forbidden
    try:
        bucket = os.environ.get('FORECAST_BUCKET', '')
        if not bucket.endswith('-forecast-dev'):
            raise ValueError('Acceptance drills require the isolated -forecast-dev bucket')
        store = GCSStore(bucket)
        now = datetime.now(timezone.utc)

        def fixture(offset=0, profile='available'):
            return generate_sample_forecast(str(uuid4()), now + timedelta(seconds=offset), profile=profile)

        baseline = fixture()
        if not publish(store, baseline):
            raise RuntimeError('Initial drill run did not become current; inspect clocks and selection')
        candidate = fixture(1)
        try:
            publish(store, candidate, fail_before_promotion=True)
        except RuntimeError as exc:
            if 'Controlled publication failure' not in str(exc):
                raise
        else:
            raise RuntimeError('Controlled failure unexpectedly succeeded')
        assert read_current(store)[0]['run_id'] == baseline['run_id']
        logging.info('gcs_drill status=success case=interrupted-publication')

        older, newer = fixture(2), fixture(3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish, GCSStore(bucket), payload) for payload in (newer, older)]
            for future in futures:
                future.result()
        assert read_current(store)[0]['run_id'] == newer['run_id']
        assert publish(store, older) is False
        assert publish(store, newer) is False
        logging.info('gcs_drill status=success case=concurrency-ordering-duplicates')

        changed = json.loads(encode(newer))
        changed['forecasts'][0]['cells'][0]['sample_index'] = 999
        try:
            publish(store, changed)
        except ValueError:
            pass
        else:
            raise RuntimeError('Conflicting duplicate was accepted')
        immutable = store.read(f"runs/{newer['run_id']}/forecast.json")
        try:
            store.write(f"runs/{newer['run_id']}/forecast.json", b'{}', expected=immutable.generation)
        except Forbidden:
            pass
        else:
            raise RuntimeError('Publisher can overwrite immutable run data')
        logging.info('gcs_drill status=success case=immutable-run-permissions')

        corrupt = fixture(4)
        path = f"runs/{corrupt['run_id']}/forecast.json"
        obj = store.write(path, encode(corrupt))
        store.write(f"runs/{corrupt['run_id']}/manifest.json", encode({
            'schema_version': '1.0', 'run_id': corrupt['run_id'], 'path': path,
            'generation': obj.generation, 'bytes': len(obj.data), 'sha256': hashlib.sha256(b'wrong').hexdigest(),
        }))
        try:
            read_run(store, corrupt['run_id'])
        except ValueError:
            pass
        else:
            raise RuntimeError('Corrupt manifest accepted')
        assert read_current(store)[0]['run_id'] == newer['run_id']
        logging.info('gcs_drill status=success case=corruption-rejected')

        for profile in ('expired', 'future'):
            try:
                publish(store, fixture(0, profile))
            except ValueError:
                pass
            else:
                raise RuntimeError('Invalid time window was promoted')
        assert read_current(store)[0]['run_id'] == newer['run_id']
        restore_run(store, baseline['run_id'])
        assert read_current(store)[0]['run_id'] == baseline['run_id']
        logging.info('gcs_drill status=success case=pointer-rollback run_id=%s', baseline['run_id'])
        restore_run(store, newer['run_id'])
        assert read_current(store)[0]['run_id'] == newer['run_id']
        logging.info('gcs_drill status=success case=rollback-recovery run_id=%s', newer['run_id'])
        return 0
    except Exception:
        logging.exception('gcs_drill status=failed')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
