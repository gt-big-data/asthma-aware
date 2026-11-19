"""A live alias must never silently mix forecast runs or bypass validation."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ingestion.geos_cf import fetch_forecast as nasa
from scripts import fetch_risk_forecast as fetch


@pytest.fixture
def remote(monkeypatch):
    init = pd.Timestamp("2026-11-18T09:00")
    times = pd.date_range(init + pd.Timedelta(minutes=30), periods=120, freq="h").values
    specs = [s for s in nasa.VARIABLES if s.name in {"pm25", "no2", "o3", "so2"}]
    names = {s.source_name: f"{s.description} ({s.units})" for s in specs}
    calls = []
    state = {"shift": False, "units": False, "grid": False}
    def fake(fn, *args, **kwargs):
        if fn is nasa._read_inventory:
            return ({**names, specs[0].source_name: "changed (ppb)"} if state["units"] else names), times, 179.75
        url, source, selection, lead = args
        calls.append((url, source, lead.start, lead.stop))
        selected = times[lead]
        if state["shift"] and lead.start == 24:
            selected = selected + np.timedelta64(24, "h")
        lon = [-84.5, -84.25] if not state["grid"] or lead.start == 0 else [-84.25, -84.0]
        return xr.DataArray(np.ones((len(selected), 2, 2), dtype="float32"), dims=("time", "lat", "lon"),
                            coords={"time": selected, "lat": [33.5, 33.75], "lon": lon})
    monkeypatch.setattr(nasa, "remote_call", fake)
    return init, specs, state, calls


def test_batches_preserve_complete_pinned_run_and_units(remote):
    init, specs, _, calls = remote
    data, url = fetch._fetch_chunks(init, specs, nasa.region_area("georgia"), latest=True)
    assert url.endswith(".latest")
    assert len(calls) == 20
    assert data.sizes == {"time": 120, "lat": 2, "lon": 2}
    assert data.time.values[0] == np.datetime64("2026-11-18T09:30")
    assert data.time.values[-1] == np.datetime64("2026-11-23T08:30")
    assert data.pm25.attrs["units"] == "ug m-3"


@pytest.mark.parametrize("problem", ["shift", "units", "grid"])
def test_alias_advancing_changed_units_or_grid_is_rejected(remote, problem):
    init, specs, state, _ = remote
    state[problem] = True
    with pytest.raises((nasa.ValidationError, ValueError)):
        fetch._fetch_chunks(init, specs, nasa.region_area("georgia"), latest=True)


def test_downloader_falls_back_without_writing_bad_candidate(remote, monkeypatch, tmp_path):
    init, specs, _, _ = remote
    good, _ = fetch._fetch_chunks(init, specs, nasa.region_area("georgia"), latest=True)
    monkeypatch.setattr(nasa, "_newest_run", lambda: init)
    attempts = []
    def candidate(current, specs, area, *, latest):
        attempts.append((current, latest))
        if current == init:
            raise nasa.ValidationError("Malformed latest run")
        delta = current - init
        return good.assign_coords(time=good.time.values + delta.to_timedelta64()), nasa.collection_url(nasa.AQC_COLLECTION, nasa.run_id_for(current))
    monkeypatch.setattr(fetch, "_fetch_chunks", candidate)
    output = fetch.download(tmp_path)
    assert "20261117_09z" in output.name
    assert len(list(tmp_path.glob("*.nc"))) == 1
    assert attempts == [(init, True), (init, False), (init - pd.Timedelta(days=1), False)]
