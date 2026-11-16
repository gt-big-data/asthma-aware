"""Explicit forecast selection rollback. Pause the publishing schedule first."""
import argparse
import logging
import os
import sys

from forecast_job.publication import restore_run
from forecast_job.storage import GCSStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--allow-regression', required=True, action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        payload = restore_run(GCSStore(os.environ.get('FORECAST_BUCKET')), args.run_id)
        logging.info('rollback status=success run_id=%s source=sample valid_until=%s',
                     payload['run_id'], payload['valid_until'])
        return 0
    except Exception:
        logging.exception('rollback status=failed')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
