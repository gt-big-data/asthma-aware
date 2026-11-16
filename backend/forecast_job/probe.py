"""Run as a workload identity to verify real bucket access, denial, and freshness."""

import argparse
from datetime import datetime, timezone
import logging
import os
import sys
from uuid import uuid4

from forecast_job.publication import read_current, verify_freshness
from forecast_job.storage import GCSStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect', required=True, choices=['read-only', 'no-access', 'fresh'])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    from google.api_core.exceptions import Forbidden
    try:
        store = GCSStore(os.environ.get('FORECAST_BUCKET'))
        if args.expect == 'no-access':
            try:
                store.read('current.json')
            except Forbidden:
                logging.info('permission_probe status=success expected=no-access')
                return 0
            raise RuntimeError('Identity unexpectedly read forecast objects')
        payload, _ = read_current(store)
        if args.expect == 'fresh':
            verify_freshness(payload)
            logging.info('freshness_probe status=success source=sample run_id=%s', payload['run_id'])
            return 0
        try:
            store.write(f'permission-probes/{uuid4()}.json', b'{"source":"permission-probe"}')
        except Forbidden:
            logging.info('permission_probe status=success expected=read-only run_id=%s', payload['run_id'])
            return 0
        raise RuntimeError('API identity unexpectedly wrote forecast objects; inspect permission-probes prefix')
    except Exception:
        logging.exception('permission_or_freshness_probe status=failed expected=%s', args.expect)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
