"""Add checkpoint predictors to a surface forecast without mixing NASA runs."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from ingestion.geos_cf import fetch_forecast as nasa
from scripts.fetch_risk_forecast import _fetch_chunks


def enrich(source: Path, required: set[str], output: Path) -> Path:
    specs = {spec.name: spec for spec in nasa.VARIABLES}
    if unknown := required - specs.keys():
        raise ValueError(f"Unknown NASA model predictors: {sorted(unknown)}")
    with xr.open_dataset(source) as opened:
        data = opened.load()
    init = pd.Timestamp(data.attrs["initialization_time"]).tz_localize(None)
    expected = pd.date_range(init + pd.Timedelta(minutes=30), periods=120, freq="h").values
    if not np.array_equal(data.time.values, expected):
        raise nasa.ValidationError("Model inputs require a complete, pinned 120-hour forecast")
    if data.attrs["forecast_run"] != nasa.run_id_for(init):
        raise nasa.ValidationError("Forecast run disagrees with initialization time")
    missing = required - set(data.data_vars)
    if not missing:
        nasa.validate_forecast(data, 120, [specs[name] for name in sorted(required)])
        return source
    original_urls = data.attrs.get("source_urls", [])
    if isinstance(original_urls, str):
        original_urls = json.loads(original_urls) if original_urls.startswith("[") else [original_urls]
    urls = set(original_urls) or {
        nasa.collection_url(data[name].attrs["source_collection"], nasa.run_id_for(init))
        for name in data.data_vars}
    for collection in sorted({specs[name].collection for name in missing}):
        needed = [specs[name] for name in sorted(missing) if specs[name].collection == collection]
        errors = []
        for latest in (True, False):
            try:
                extra, url = _fetch_chunks(init, needed, nasa.region_area("georgia"), latest=latest)
                # Exact coordinates prevent xarray's default union from filling mismatches with NaN.
                data = xr.merge([data, extra], join="exact", compat="equals")
                urls.add(url)
                break
            except (nasa.ValidationError, nasa.ForecastUnavailableError, OSError, ValueError) as error:
                errors.append(str(error))
        else:
            raise nasa.ForecastUnavailableError(f"Cannot obtain same-run {collection} predictors: {errors}")
    nasa.validate_forecast(data, 120, [specs[name] for name in sorted(required)])
    metadata = nasa.build_metadata(data, init, nasa.region_area("georgia"), slice(0, 120), datetime.now(timezone.utc))
    metadata.update(region="georgia_models", source_urls=sorted(urls),
                    download_method="Same-run model predictors in verified 24-hour batches")
    return nasa.write_outputs(data, metadata, output)[0]


if __name__ == "__main__":
    from scripts.downscale_forecast import TARGETS, required_variables
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--models", nargs="+", choices=tuple(TARGETS))
    parser.add_argument("--output-dir", type=Path, default=Path("data/geos_cf"))
    args = parser.parse_args()
    print(enrich(args.input, required_variables(args.model_dir, args.models), args.output_dir))
