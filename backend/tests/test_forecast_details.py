from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import xarray as xr

from app.services.geos_dashboard import ForecastInput, PublishedForecast, dashboard_forecast, with_forecast_details
from scripts.publish_dashboard_forecast import prepare_run


def test_source_horizon_is_distinct_from_displayed_horizon_and_missing_scores():
    issued = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
    run = PublishedForecast(source_run="run", issued_at=issued,
        times=[issued + timedelta(hours=i, minutes=30) for i in range(120)],
        latitudes=[33.75], longitudes=[-84.5], pm25=[[None] for _ in range(120)],
        input_sources=[ForecastInput(variable="o3", source_run="run", issued_at=issued)])
    now = issued + timedelta(hours=4)
    result = with_forecast_details(dashboard_forecast(run, 33.75, -84.5, now), run, now)
    info = result.forecast_details
    assert info.forecast_start == issued
    assert info.forecast_end == issued + timedelta(hours=120)
    assert info.source_remaining_hours == 116
    assert info.displayed_hours == 96 and info.scored_hours == 0
    assert info.displayed_through == now + timedelta(hours=96)
    assert info.input_sources[0].issued_at == issued


def test_older_runs_report_actual_remaining_coverage_and_legacy_sources_stay_unknown():
    issued = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
    run = PublishedForecast(source_run="run", issued_at=issued,
        times=[issued + timedelta(hours=i, minutes=30) for i in range(120)],
        latitudes=[33.75], longitudes=[-84.5], pm25=[[10.] for _ in range(120)])
    now = issued + timedelta(hours=32)
    result = with_forecast_details(dashboard_forecast(run, 33.75, -84.5, now), run, now)
    assert result.forecast_details.source_remaining_hours == 88
    assert result.forecast_details.displayed_hours == result.forecast_details.scored_hours == 88
    assert result.forecast_details.input_sources == []


def test_publisher_records_per_variable_timestamps_and_collection(tmp_path):
    issued = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1)
    times = pd.date_range(issued.tz_localize(None) + pd.Timedelta(minutes=30), periods=120, freq="h")
    variables = {name: (("time", "lat", "lon"), np.ones((120, 2, 2), dtype="float32"),
        {"units": "ug m-3" if name == "pm25" else "mol mol-1", "source_collection": "aqc"})
        for name in ("o3", "no2", "so2", "pm25")}
    data = xr.Dataset(variables, coords={"time": times, "lat": [33.5, 33.75], "lon": [-84.5, -84.25]},
        attrs={"initialization_time": issued.isoformat(), "forecast_run": "run"})
    data.so2.attrs.update(initialization_time=(issued - pd.Timedelta(hours=24)).isoformat(), forecast_run="other-run")
    path = tmp_path / "forecast.nc"
    data.to_netcdf(path)
    result = prepare_run(path)
    sources = {source.variable: source for source in result.input_sources}
    assert sources["o3"].issued_at == issued.to_pydatetime()
    assert sources["so2"].issued_at == (issued - pd.Timedelta(hours=24)).to_pydatetime()
    assert sources["so2"].source_run == "other-run"
    assert sources["pm25"].collection == "aqc"
    assert PublishedForecast.model_validate_json(result.model_dump_json()).input_sources == result.input_sources
