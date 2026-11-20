"""Model predictors must share the surface run's exact timestamps and grid."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ingestion.geos_cf import fetch_forecast as nasa
from scripts import fetch_model_forecast as fetch


@pytest.fixture
def forecasts(tmp_path):
    specs = {spec.name: spec for spec in nasa.VARIABLES}
    def dataset(names):
        return xr.Dataset({name: (("time", "lat", "lon"), np.ones((120, 2, 2), dtype="float32"),
            {"units": specs[name].units, "source_name": specs[name].source_name,
             "source_collection": specs[name].collection}) for name in names},
            coords={"time": pd.date_range("2026-11-18T09:30", periods=120, freq="h"),
                    "lat": [33., 33.25], "lon": [-84.5, -84.25]})
    data = dataset(["o3"])
    data = data.assign_coords(lead_time=("time", data.time.values - np.datetime64("2026-11-18T09:00")))
    data.attrs.update(initialization_time="2026-11-18T09:00Z", forecast_run="20261118_09z",
                      source_urls=["https://example.test/aqc.latest"])
    source = tmp_path / "source.nc"
    data.to_netcdf(source)
    return source, dataset(["air_temperature", "specific_humidity", "surface_pressure", "pbl_height"])


def test_fetches_only_missing_predictors_from_pinned_run(forecasts, monkeypatch, tmp_path):
    source, weather = forecasts
    calls = []
    def chunks(init, specs, area, *, latest):
        calls.append((init, {spec.name for spec in specs}, latest))
        assert {spec.collection for spec in specs} == {nasa.MET_COLLECTION}
        return weather, "https://example.test/met.latest"
    monkeypatch.setattr(fetch, "_fetch_chunks", chunks)
    required = {"o3", *weather.data_vars}
    output = fetch.enrich(source, required, tmp_path)
    assert calls == [(pd.Timestamp("2026-11-18T09:00"), set(weather.data_vars), True)]
    with xr.open_dataset(output) as data:
        assert set(data.data_vars) == required
        assert data.attrs["forecast_run"] == "20261118_09z"
        assert data.o3.attrs["units"] == "mol mol-1"
        assert "https://example.test/aqc.latest" in data.attrs["source_urls"]
    assert fetch.enrich(output, required, tmp_path) == output
    assert len(calls) == 1


@pytest.mark.parametrize("axis", ["time", "lat", "lon"])
def test_mixed_run_or_grid_cannot_be_merged(forecasts, monkeypatch, tmp_path, axis):
    source, weather = forecasts
    changed = weather.assign_coords({axis: weather[axis] + (np.timedelta64(1, "h") if axis == "time" else .25)})
    monkeypatch.setattr(fetch, "_fetch_chunks", lambda *a, **kw: (changed, "https://example.test"))
    with pytest.raises(nasa.ForecastUnavailableError, match="same-run"):
        fetch.enrich(source, {"o3", *weather.data_vars}, tmp_path)
    assert not list(tmp_path.glob("geos_cf_*.nc"))
