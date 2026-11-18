"""Download a complete surface-pollutant forecast, falling back from incomplete runs."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ingestion.geos_cf import fetch_forecast as nasa


def download(output=Path("data/geos_cf")):
    area = nasa.region_area("georgia")
    newest = nasa.resolve_forecast_run()
    specs = [spec for spec in nasa.VARIABLES if spec.name in {"o3", "no2", "so2", "pm25"}]
    output.mkdir(parents=True, exist_ok=True)
    for offset in range(4):
        init = newest - pd.Timedelta(days=offset)
        run_id = nasa.run_id_for(init)
        try:
            data = nasa.fetch_collection(nasa.AQC_COLLECTION, run_id, specs, area.bbox, slice(0, 120))
            nasa.validate_forecast(data, 120, specs)
            if data.time.values[0] != (init + pd.Timedelta(minutes=30)).to_datetime64():
                raise nasa.ValidationError("Forecast does not begin at its initialization midpoint")
            data = data.assign_coords(lead_time=("time", data.time.values - init.to_datetime64()))
            metadata = nasa.build_metadata(data, init, area, slice(0, 120), datetime.now(timezone.utc))
            metadata["source_urls"] = [nasa.collection_url(nasa.AQC_COLLECTION, run_id)]
            metadata["region"] = "georgia_risk"
            data.attrs.update(source_run=run_id)
            return nasa.write_outputs(data, metadata, output)[0]
        except (nasa.ValidationError, nasa.ForecastUnavailableError, OSError) as error:
            nasa.logger.warning("Could not use %s for risk: %s", run_id, error)
    raise nasa.ForecastUnavailableError("No complete surface risk forecast was available")


if __name__ == "__main__":
    print(download())
