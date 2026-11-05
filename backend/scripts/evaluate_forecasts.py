"""Score shipped ConvLSTM forecasts for SO2, NDVI, and NO2.

Run from the backend directory:

    python scripts/evaluate_forecasts.py

Writes ``app/ml/eval_runs/<timestamp>/metrics.json`` and prints one line per
channel and horizon. Exits with status 1 when the raster stacks or the
checkpoint are missing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.constants import FEATURE_ORDER  # noqa: E402
from app.ml.forecast_eval import (  # noqa: E402
    MissingForecastInputs,
    evaluate_rasters,
)


def _json_ready(value):
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float):
        return value
    return value


def _format_correlation(correlation):
    if correlation is None:
        return "undefined"
    return f"{correlation:.3f}"


def _print_report(report):
    print(report["label"])
    print(
        f"days={report['n_days']} origins={report['n_origins']} "
        f"band0={report['band0_date']} end={report['end_date']}"
    )
    for warning in report["warnings"]:
        print(f"warning: {warning}")
    for name in FEATURE_ORDER:
        channel = report["channels"][name]
        print(
            f"{name} raw min={channel['min']} max={channel['max']} "
            f"nonfinite={channel['nonfinite_fraction']:.4f} "
            f"scaled min={channel['scaled_min']} max={channel['scaled_max']}"
        )
    for horizon, scores in report["horizons"].items():
        for name in FEATURE_ORDER:
            score = scores[name]
            print(
                f"{horizon} {name}  "
                f"mae={score['mae']:.4f}  "
                f"persistence={score['mae_persistence']:.4f}  "
                f"wrong_day={score['mae_wrong_day']:.4f}  "
                f"corr={_format_correlation(score['correlation'])}  "
                f"pass={score['passes_not_random']}"
            )


def main(argv=None) -> int:
    del argv
    try:
        report = evaluate_rasters()
    except MissingForecastInputs as exc:
        print(exc)
        return 1

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = BACKEND_DIR / "app" / "ml" / "eval_runs" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "metrics.json"
    output_path.write_text(json.dumps(_json_ready(report), indent=2), encoding="utf-8")

    _print_report(report)
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
