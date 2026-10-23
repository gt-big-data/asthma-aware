import numpy as np
import pandas as pd
import pytest

from ingestion.geos_cf import fetch_analysis as fa
from ingestion.geos_cf import fetch_forecast as ff
from test_geos_cf_fetch_forecast import fake_remote

ATLANTA = ff.point_area(33.75, -84.39)
WINDOW = ff.TimeWindow(pd.Timestamp("2026-10-15T12:00"), pd.Timestamp("2026-10-19T12:00"))


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(ff, "open_remote", lambda url: fake_remote(next(c for c in ff.COLLECTIONS if c in url)))
    monkeypatch.setattr(ff, "remote_call", lambda fn, *args, **kwargs: fn(*args))


def fake_cloud_copy(area, times):
    """Like the real Zarr copy: no specific humidity, and a 12-hour hole in everything else."""
    out = {}
    for spec in ff.VARIABLES:
        if spec.source_name == "q":
            continue
        src = fake_remote(spec.collection)[spec.source_name].isel(lev=0).sel(**ff._grid_selection(area.bbox))
        hole = (src.time >= np.datetime64("2026-10-16T00:00")) & (src.time < np.datetime64("2026-10-16T12:00"))
        out[spec.name] = src.where(~hole)
    return out


def test_time_indices_select_hours_overlapping_window():
    times = pd.date_range("2026-10-15T09:30", periods=120, freq="h").values
    assert fa.time_indices(times, WINDOW) == slice(3, 99)  # 12:30 .. 11:30 on the 19th


def test_native_grid_for_point_and_bbox():
    lat, lon = fa.native_grid(ATLANTA)
    assert lat.tolist() == [33.75] and lon.tolist() == [-84.5]
    lat, lon = fa.native_grid(ff.bbox_area(33.4, 34.2, -84.9, -83.9))
    assert lat.tolist() == [33.5, 33.75, 34.0, 34.25] and len(lon) == 5


def test_runs_split_gaps_into_bounded_chunks():
    assert fa._runs(np.array([3, 4, 5, 9, 10]), max_len=2) == [slice(3, 5), slice(5, 6), slice(9, 11)]


def test_cloud_copy_is_used_and_gaps_filled_from_opendap(offline, monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "read_zarr", fake_cloud_copy)
    area = ff.bbox_area(33.4, 34.2, -84.9, -83.9)
    ds, urls, from_opendap = fa.build_analysis(area, WINDOW, workers=2, cache_dir=tmp_path / "cache")

    assert dict(ds.sizes) == {"time": 96, "lat": 4, "lon": 5}
    assert from_opendap["pm25"] == 12  # just the hole
    assert from_opendap["specific_humidity"] == 96  # not in the cloud copy at all
    assert not bool(ds.to_array().isnull().any())
    truth = fake_remote(ff.AQC_COLLECTION)["pm25_rh35"].isel(lev=0).sel(time=ds.time, lat=ds.lat, lon=ds.lon)
    np.testing.assert_allclose(ds["pm25"].values, truth.values)
    assert urls[0] == fa.ZARR_URL
    assert fa.validate_analysis(ds, 96)["pm25"] == 0


def test_unreadable_hours_are_isolated_not_fatal(offline, monkeypatch):
    bad = {5, 6, 40}

    def flaky(fn, *args, what="", retries=3):
        lead = args[3]
        if any(lead.start <= b < lead.stop for b in bad):
            raise OSError("502 Proxy Error")
        return fn(*args)

    monkeypatch.setattr(ff, "remote_call", flaky)
    spec = next(s for s in ff.VARIABLES if s.name == "no2")
    data = fa._fetch_chunk(fa.analysis_url(spec.collection), spec, ff._grid_selection(ATLANTA.bbox), slice(0, 48), None)
    assert data.sizes["time"] == 45
