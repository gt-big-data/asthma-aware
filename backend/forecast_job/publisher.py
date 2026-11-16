"""Explicit GCS synthetic publishing entry point; no database or model access."""

import argparse
from datetime import datetime, timezone
import logging
import os
import sys
from uuid import uuid4

from forecast_job.sample import generate_sample_forecast
from forecast_job.publication import publish
from forecast_job.storage import GCSStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=['sample'])
    parser.add_argument('--fail-before-promotion', action='store_true')
    parser.add_argument('--fixture-profile', choices=['available', 'expired', 'future'], default='available')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    run_id = str(uuid4())
    try:
        logging.info('start run_id=%s source=sample', run_id)
        now = datetime.now(timezone.utc)
        payload = generate_sample_forecast(run_id, now, profile=args.fixture_profile)
        promoted = publish(GCSStore(os.environ.get('FORECAST_BUCKET')), payload, args.fail_before_promotion)
        logging.info('completion run_id=%s status=success promoted=%s source=sample', run_id, promoted)
        return 0
    except Exception:
        logging.exception('completion run_id=%s status=failed', run_id)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
