"""Run from backend/: python -m forecast_job --mode sample --output-dir data/forecast-job."""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from uuid import uuid4

from forecast_job.sample import generate_sample_forecast

logger = logging.getLogger("forecast_job")


def run_once(mode: str, output_dir: Path) -> Path:
    run_id = str(uuid4())
    logger.info("start run_id=%s mode=%s", run_id, mode)
    try:
        if mode != "sample":
            raise ValueError("Only explicit sample mode is implemented")
        logger.info("processing run_id=%s source=sample", run_id)
        artifact = generate_sample_forecast(run_id, datetime.now(timezone.utc))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"forecast-{run_id}.json"
        # Exclusive creation avoids silently overwriting any existing artifact.
        with output_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(artifact, stream, indent=2, allow_nan=False)
            stream.write("\n")
        logger.info("output run_id=%s path=%s bytes=%d", run_id, output_path, output_path.stat().st_size)
        logger.info("completion run_id=%s status=success", run_id)
        return output_path
    except Exception:
        logger.exception("completion run_id=%s status=failed", run_id)
        raise


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("sample",), help="Explicit opt-in to synthetic data")
    parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("FORECAST_OUTPUT_DIR", "/tmp/forecast")))
    args = parser.parse_args(argv)
    try:
        run_once(args.mode, args.output_dir)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
