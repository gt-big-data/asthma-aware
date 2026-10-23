import datetime as dt
import json
import time

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ingestion.geos_cf import fetch_forecast as ff

INIT = pd.Timestamp("2026-10-15T09:00")
GEORGIA = ff.REGIONS["georgia"]
FULL_RUN = slice(0, 120)


def fake_remote(collection, hours=120):
    """A small stand-in for a GEOS-CF OPeNDAP collection, shaped like the real one."""
    times = pd.date_range(INIT + ff.TAVG_OFFSET, periods=hours, freq="h")
    lat = np.arange(25.0, 40.01, 0.25)
    lon = np.arange(-90.0, -75.01, 0.25)
    shape = (hours, 1, lat.size, lon.size)
    data_vars = {}
    for spec in ff.VARIABLES:
        if spec.collection == collection:
            data_vars[spec.source_name] = (
                ("time", "lev", "lat", "lon"),
                np.random.default_rng(0).random(shape, dtype=np.float32),
                {"long_name": f"{spec.source_name} thing ({spec.units}) "},
            )
    return xr.Dataset(data_vars, coords={"time": times, "lev": [72.0], "lat": lat, "lon": lon})


@pytest.fixture
def offline(monkeypatch):
    def open_remote(url):
        return fake_remote(next(c for c in ff.COLLECTIONS if c in url))

    # Run the remote readers in-process so they see the fake open_remote.
    monkeypatch.setattr(ff, "open_remote", open_remote)
    monkeypatch.setattr(ff, "remote_call", lambda fn, *args, **kwargs: fn(*args))


@pytest.fixture
def forecast(offline):
    return ff.build_forecast(INIT, ff.region_area("georgia"), FULL_RUN)


def test_parse_units_reads_trailing_parenthetical():
    assert ff.parse_units("ozone (o3, mw=48.0 g mol-1) mole fraction in dry air (mol mol-1) ") == "mol mol-1"
    assert ff.parse_units("relative_humidity_after_moist (1)") == "1"
    assert ff.parse_units("no units here") is None


def test_infer_init_time_from_centered_tavg_stamp():
    times = pd.date_range("2026-10-15T09:30", periods=3, freq="h").values
    assert ff.infer_init_time(times) == INIT
    assert ff.run_id_for(INIT) == "20261015_09z"


def test_build_forecast_subsets_georgia_and_keeps_metadata(forecast):
    assert dict(forecast.sizes) == {"time": 120, "lat": 20, "lon": 20}
    assert float(forecast.lat.min()) == 30.25 and float(forecast.lat.max()) == 35.0
    assert float(forecast.lon.min()) == -85.5 and float(forecast.lon.max()) == -80.75
    assert forecast["pm25"].attrs["source_name"] == "pm25_rh35"
    assert forecast["pm25"].attrs["units"] == "ug m-3"
    assert forecast["lead_time"].values[0] == np.timedelta64(30, "m")
    ff.validate_forecast(forecast, expected_hours=120)


def test_validation_reports_every_problem(forecast):
    broken = forecast.drop_vars("o3").isel(time=slice(0, 100))
    broken["no2"] = broken["no2"] * np.nan
    broken["co"].attrs["units"] = "ppb"
    with pytest.raises(ff.ValidationError) as exc:
        ff.validate_forecast(broken, expected_hours=120)
    message = str(exc.value)
    assert "expected 120 forecast hours, got 100" in message
    assert "'o3' (o3) is missing" in message
    assert "'no2' is entirely NaN" in message
    assert "expected 'mol mol-1', got 'ppb'" in message


def test_validation_rejects_unordered_times_and_empty_subset(forecast):
    reordered = forecast.isel(time=[1, 0] + list(range(2, 120)))
    with pytest.raises(ff.ValidationError, match="not strictly increasing"):
        ff.validate_forecast(reordered, expected_hours=120)

    empty = forecast.isel(lat=slice(0, 0))
    with pytest.raises(ff.ValidationError, match="'lat' is empty"):
        ff.validate_forecast(empty, expected_hours=120)


def test_validation_flags_hours_that_are_all_nan(forecast):
    forecast["pm25"][5] = np.nan
    with pytest.raises(ff.ValidationError, match="1 forecast hour"):
        ff.validate_forecast(forecast, expected_hours=120)


def test_write_outputs_round_trip(forecast, tmp_path):
    downloaded_at = dt.datetime(2026, 10, 15, 22, tzinfo=dt.timezone.utc)
    metadata = ff.build_metadata(forecast, INIT, ff.region_area("georgia"), FULL_RUN, downloaded_at)
    nc_path, json_path = ff.write_outputs(forecast, metadata, tmp_path)

    assert nc_path.name == "geos_cf_georgia_20261015_09z.nc"
    sidecar = json.loads(json_path.read_text())
    assert sidecar["source"] == "NASA GEOS-CF"
    assert sidecar["initialization_time"] == "2026-10-15T09:00:00Z"
    assert sidecar["downloaded_at"] == "2026-10-15T22:00:00Z"
    assert len(sidecar["valid_times"]) == 120
    assert sidecar["variable_source_names"]["pm10"] == "pm10_rh35"
    assert sidecar["units"]["air_temperature"] == "k"

    with xr.open_dataset(nc_path) as ds:
        assert ds.attrs["forecast_run"] == "20261015_09z"
        assert json.loads(ds.attrs["region_bbox"]) == GEORGIA
        assert ds["so2"].attrs["source_name"] == "so2"
        np.testing.assert_array_equal(ds["time"].values, forecast["time"].values)
        np.testing.assert_allclose(ds["pm25"].values, forecast["pm25"].values)


def test_resolve_falls_back_when_newest_run_incomplete(offline, monkeypatch):
    available = {"20261014_09z"}
    monkeypatch.setattr(ff, "_run_available", lambda run_id: run_id in available)
    assert ff.resolve_forecast_run() == pd.Timestamp("2026-10-14T09:00")


def test_resolve_raises_when_nothing_available(offline, monkeypatch):
    monkeypatch.setattr(ff, "_run_available", lambda run_id: False)
    with pytest.raises(ff.ForecastUnavailableError):
        ff.resolve_forecast_run(lookback_days=2)


def test_resolve_pinned_run(monkeypatch):
    monkeypatch.setattr(ff, "_run_available", lambda run_id: run_id == "20261001_09z")
    assert ff.resolve_forecast_run(dt.date(2026, 10, 1)) == pd.Timestamp("2026-10-01T09:00")
    with pytest.raises(ff.ForecastUnavailableError):
        ff.resolve_forecast_run(dt.date(2026, 10, 2))


def test_run_with_timeout_kills_stalled_request():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        ff.run_with_timeout(time.sleep, 60, timeout=1)
    assert time.monotonic() - started < 30


def test_run_with_timeout_returns_results_and_reraises_errors():
    assert ff.run_with_timeout(abs, -3, timeout=60) == 3
    with pytest.raises(FileNotFoundError):
        ff.run_with_timeout(open, "/nonexistent/geos_cf", timeout=60)


def utc(text):
    return pd.Timestamp(text)


def test_point_area_snaps_to_containing_grid_cell(offline):
    area = ff.point_area(33.75, -84.39)  # downtown Atlanta
    assert area.name == "point_33.75N_84.39W"
    assert area.bbox == {"min_lat": 33.75, "max_lat": 33.75, "min_lon": -84.5, "max_lon": -84.5}
    ds = ff.build_forecast(INIT, area, FULL_RUN)
    assert dict(ds.sizes) == {"time": 120, "lat": 1, "lon": 1}
    assert float(ds.lat) == 33.75 and float(ds.lon) == -84.5


def test_point_and_bbox_must_be_in_georgia():
    with pytest.raises(ValueError, match="outside Georgia"):
        ff.point_area(40.7, -74.0)
    with pytest.raises(ValueError, match="outside Georgia"):
        ff.bbox_area(33.0, 36.0, -85.0, -84.0)
    with pytest.raises(ValueError, match="min <= max"):
        ff.bbox_area(34.0, 33.0, -85.0, -84.0)


def test_bbox_area_subsets_cells_touching_box(offline):
    ds = ff.build_forecast(INIT, ff.bbox_area(33.4, 34.2, -84.9, -83.9), FULL_RUN)
    assert ds["lat"].values.tolist() == [33.5, 33.75, 34.0, 34.25]
    assert ds["lon"].values.tolist() == [-85.0, -84.75, -84.5, -84.25, -84.0]


def test_parse_time_converts_to_utc():
    assert ff.parse_time("2026-10-16T15:00", "America/New_York") == utc("2026-10-16T19:00")
    assert ff.parse_time("2026-10-16T15:00-04:00") == utc("2026-10-16T19:00")
    assert ff.parse_time("2026-10-16") == utc("2026-10-16T00:00")


def test_lead_slice_for_times_and_intervals():
    # Step k is the mean over [09:00 + k h, 10:00 + k h) on the run day.
    assert ff.lead_slice(INIT) == slice(0, 120)
    assert ff.lead_slice(INIT, hours=24) == slice(0, 24)
    assert ff.lead_slice(INIT, ff.TimeWindow(utc("2026-10-15T09:00"), utc("2026-10-15T09:00"))) == slice(0, 1)
    assert ff.lead_slice(INIT, ff.TimeWindow(utc("2026-10-15T19:45"), utc("2026-10-15T19:45"))) == slice(10, 11)
    # 15:00-18:00 local is 19:00-22:00 UTC -> three hourly means
    assert ff.lead_slice(INIT, ff.TimeWindow(utc("2026-10-15T19:00"), utc("2026-10-15T22:00"))) == slice(10, 13)
    assert ff.lead_slice(INIT, ff.TimeWindow(utc("2026-10-15T19:30"), utc("2026-10-15T21:10"))) == slice(10, 13)
    assert ff.lead_slice(INIT, ff.TimeWindow(utc("2026-10-17T09:00"))) == slice(48, 120)


def test_run_covers():
    assert ff.run_covers(INIT, ff.TimeWindow(utc("2026-10-20T08:59"), utc("2026-10-20T08:59")))
    assert not ff.run_covers(INIT, ff.TimeWindow(utc("2026-10-20T09:00"), utc("2026-10-20T09:00")))
    assert not ff.run_covers(INIT, ff.TimeWindow(utc("2026-10-15T08:00"), utc("2026-10-15T08:00")))
    assert ff.latest_init_at_or_before(utc("2026-10-12T08:00")) == utc("2026-10-11T09:00")
    assert ff.latest_init_at_or_before(utc("2026-10-12T09:00")) == utc("2026-10-12T09:00")


def test_resolve_picks_freshest_run_covering_a_past_time(offline, monkeypatch):
    monkeypatch.setattr(ff, "_run_available", lambda run_id: True)
    t = utc("2026-10-12T19:00")
    assert ff.resolve_forecast_run(window=ff.TimeWindow(t, t)) == utc("2026-10-12T09:00")
    # .latest (fake) is 20261015_09z, so a time inside it uses it.
    t = utc("2026-10-17T19:00")
    assert ff.resolve_forecast_run(window=ff.TimeWindow(t, t)) == INIT


def test_resolve_falls_back_to_older_run_that_still_covers_window(offline, monkeypatch):
    monkeypatch.setattr(ff, "_run_available", lambda run_id: run_id == "20261011_09z")
    t = utc("2026-10-12T19:00")
    assert ff.resolve_forecast_run(window=ff.TimeWindow(t, t)) == utc("2026-10-11T09:00")


def test_resolve_rejects_windows_no_run_covers(offline, monkeypatch):
    monkeypatch.setattr(ff, "_run_available", lambda run_id: True)
    beyond = utc("2026-10-21T00:00")
    with pytest.raises(ff.ForecastUnavailableError, match="No single run covers"):
        ff.resolve_forecast_run(window=ff.TimeWindow(beyond, beyond))
    with pytest.raises(ff.ForecastUnavailableError, match="No single run covers"):
        ff.resolve_forecast_run(window=ff.TimeWindow(utc("2026-10-10T12:00"), utc("2026-10-16T12:00")))
    with pytest.raises(ff.ForecastUnavailableError, match="does not include"):
        ff.resolve_forecast_run(dt.date(2026, 10, 15), window=ff.TimeWindow(beyond, beyond))


def test_run_point_and_interval_end_to_end(offline, tmp_path):
    window = ff.TimeWindow(ff.parse_time("2026-10-16T15:00", "America/New_York"), ff.parse_time("2026-10-16T18:00", "America/New_York"))
    nc_path = ff.run(ff.point_area(33.75, -84.39), tmp_path, window=window)

    assert nc_path.name == "geos_cf_point_33.75N_84.39W_20261015_09z_h034-037.nc"
    with xr.open_dataset(nc_path) as ds:
        assert dict(ds.sizes) == {"time": 3, "lat": 1, "lon": 1}
        assert [str(t)[:16] for t in ds["time"].values] == ["2026-10-16T19:30", "2026-10-16T20:30", "2026-10-16T21:30"]
        assert json.loads(ds.attrs["requested_point"]) == {"lat": 33.75, "lon": -84.39}
        assert ds.attrs["lead_hour_range"].tolist() == [34, 37]


def test_cli_rejects_bad_location_and_time(capsys):
    for argv in (["--point", "40.7,-74.0"], ["--point", "33.7"], ["--end", "2026-10-16"],
                 ["--start", "2026-10-16T12:00", "--end", "2026-10-16T10:00"], ["--time", "x", "--hours", "3"]):
        with pytest.raises(SystemExit) as exc:
            ff.main(argv)
        assert exc.value.code == 2
