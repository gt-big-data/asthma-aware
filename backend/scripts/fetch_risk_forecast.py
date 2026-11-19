"""Download a complete surface forecast in run-verified 24-hour batches."""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from ingestion.geos_cf import fetch_forecast as nasa


def _fetch_chunks(init, specs, area, *, latest):
    """A moving alias is usable only if every batch matches the pinned run."""
    run_id = nasa.run_id_for(init)
    url = nasa.collection_url(nasa.AQC_COLLECTION, None if latest else run_id)
    names, times, lon_max = nasa.remote_call(nasa._read_inventory, url, what=f"Checking {url}", retries=2)
    expected = pd.date_range(init + pd.Timedelta(minutes=30), periods=120, freq="h").values
    if not np.array_equal(times, expected):
        raise nasa.ValidationError("Forecast inventory differs from the pinned run timeline")
    if lon_max > 180:
        raise nasa.ValidationError("Expected -180..180 longitudes")
    variables = []
    for spec in specs:
        if spec.source_name not in names or nasa.parse_units(names[spec.source_name]) != spec.units:
            raise nasa.ValidationError(f"Missing variable or changed units: {spec.source_name}")
        parts = []
        for start in range(0, 120, 24):
            stop = start + 24
            data = nasa.remote_call(
                nasa._read_variable, url, spec.source_name, nasa._grid_selection(area.bbox), slice(start, stop),
                what=f"Downloading {spec.source_name} hours {start}..{stop - 1}", retries=2,
            )
            if not np.array_equal(data.time.values, expected[start:stop]):
                raise nasa.ValidationError("Batch timestamps differ from the pinned run; refusing mixed or malformed data")
            data.attrs = {"units": spec.units, "long_name": spec.description, "source_name": spec.source_name,
                          "source_collection": spec.collection, "source_long_name": names[spec.source_name]}
            data.encoding = {}
            parts.append(data)
            nasa.logger.info("  %s hours %d..%d validated for %s", spec.name, start, stop - 1, run_id)
        variables.append(xr.concat(parts, dim="time", join="exact").rename(spec.name))
    data = xr.merge(variables, join="exact").transpose("time", "lat", "lon")
    nasa.validate_forecast(data, 120, specs)
    return data, url


def download(output=Path("data/geos_cf")):
    area = nasa.region_area("georgia")
    # Discover the newest AQ initialization without requiring unrelated MET/XGC collections.
    newest = nasa._newest_run()
    specs = [spec for spec in nasa.VARIABLES if spec.name in {"o3", "no2", "so2", "pm25"}]
    output.mkdir(parents=True, exist_ok=True)
    for offset in range(4):
        init = newest - pd.Timedelta(days=offset)
        run_id = nasa.run_id_for(init)
        # Dated endpoint metadata can lag the live alias. Its data remain safe to
        # use because the inventory AND every downloaded batch must match init.
        for latest in ([True, False] if offset == 0 else [False]):
            try:
                data, source_url = _fetch_chunks(init, specs, area, latest=latest)
                data = data.assign_coords(lead_time=("time", data.time.values - init.to_datetime64()))
                metadata = nasa.build_metadata(data, init, area, slice(0, 120), datetime.now(timezone.utc))
                metadata["source_urls"] = [source_url]
                metadata["region"] = "georgia_risk"
                metadata["download_method"] = "24-hour batches; every timestamp verified against pinned run"
                return nasa.write_outputs(data, metadata, output)[0]
            except (nasa.ValidationError, nasa.ForecastUnavailableError, OSError, ValueError) as error:
                nasa.logger.warning("Could not use %s (%s) for risk: %s", run_id, "latest alias" if latest else "dated endpoint", error)
    raise nasa.ForecastUnavailableError("No complete surface risk forecast was available")


if __name__ == "__main__":
    print(download())
