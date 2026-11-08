import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ingestion.geos_cf import fetch_analysis as fa
from ingestion.geos_cf import fetch_forecast as ff
from tests.geos_cf_fakes import fake_remote

ATLANTA = ff.point_area(33.75, -84.39)
WINDOW = ff.TimeWindow(pd.Timestamp("2026-10-15T12:00"), pd.Timestamp("2026-10-19T12:00"))


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(ff, "open_remote", lambda url: fake_remote(next(c for c in ff.COLLECTIONS if c in url)))
    monkeypatch.setattr(ff, "remote_call", lambda fn, *args, **kwargs: fn(*args))


def fake_cloud_copy(area, times, product=fa.V2):
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


def test_runs_split_gaps_on_a_fixed_grid():
    # Boundaries fall on multiples of max_len, whatever the gap's start.
    assert fa._runs(np.array([3, 4, 5, 9, 10]), max_len=2) == [slice(3, 4), slice(4, 6), slice(9, 10), slice(10, 11)]
    assert fa._runs(np.arange(5, 20), max_len=8) == [slice(5, 8), slice(8, 16), slice(16, 20)]


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
    times = pd.DatetimeIndex(fake_remote(spec.collection)["time"].values[:48])
    data, seconds = fa._fetch_chunk(fa.analysis_url(spec.collection), spec, ff._grid_selection(ATLANTA.bbox), slice(0, 48), times, None)
    assert data.sizes["time"] == 45 and seconds is not None


def test_cache_is_kept_and_reused_by_overlapping_requests(offline, monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "read_zarr", fake_cloud_copy)
    cache = tmp_path / "cache"
    fa.build_analysis(ATLANTA, WINDOW, workers=2, cache_dir=cache)
    assert any(cache.glob("q_*.nc"))

    calls = []
    monkeypatch.setattr(ff, "remote_call", lambda fn, *args, **kwargs: calls.append(args) or fn(*args))
    inner = ff.TimeWindow(pd.Timestamp("2026-10-16T03:00"), pd.Timestamp("2026-10-17T03:00"))  # 1 day inside WINDOW
    ds, _, from_opendap = fa.build_analysis(ATLANTA, inner, workers=2, cache_dir=cache)
    downloads = [a for a in calls if a and a[0] is not None and len(a) > 3]
    assert downloads == []  # only the inventory calls; every chunk came from the cache
    assert from_opendap["specific_humidity"] == 24
    assert not bool(ds.to_array().isnull().any())


def test_monthly_windows_take_first_days_in_local_time():
    windows = fa.monthly_windows("2025-10-01", "2026-10-01", 5, "America/New_York")
    assert len(windows) == 12
    assert windows[0].start == pd.Timestamp("2025-10-01T04:00") and windows[0].end == pd.Timestamp("2025-10-06T04:00")
    assert windows[2].start == pd.Timestamp("2025-12-01T05:00")  # EST after the DST change
    assert all(w.end - w.start in (pd.Timedelta(hours=120), pd.Timedelta(hours=119), pd.Timedelta(hours=121)) for w in windows)


def test_build_analysis_with_several_windows(offline, monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "read_zarr", fake_cloud_copy)
    windows = [
        ff.TimeWindow(pd.Timestamp("2026-10-15T12:00"), pd.Timestamp("2026-10-15T18:00")),
        ff.TimeWindow(pd.Timestamp("2026-10-16T06:00"), pd.Timestamp("2026-10-16T10:00")),  # inside the cloud hole
    ]
    ds, _, from_opendap = fa.build_analysis(ATLANTA, windows, workers=2, cache_dir=tmp_path / "cache")
    assert ds.sizes["time"] == 10
    assert from_opendap["pm25"] == 4 and from_opendap["specific_humidity"] == 10
    assert not bool(ds.to_array().isnull().any())
    assert fa.validate_analysis(ds, 10)["pm25"] == 0


def test_product_is_chosen_by_date():
    w = lambda a, b: ff.TimeWindow(pd.Timestamp(a), pd.Timestamp(b))
    assert fa.product_for([w("2023-01-01", "2023-01-06")]) is fa.V1
    assert fa.product_for([w("2025-10-01", "2025-10-06")]) is fa.V2
    with pytest.raises(ValueError, match="spans the switch"):
        fa.product_for([w("2025-07-01", "2025-07-06"), w("2025-10-01", "2025-10-06")])


def test_v1_keeps_v2_columns_and_leaves_pm10_empty(monkeypatch, tmp_path):
    v1_collections = list(dict.fromkeys(s.collection for s in fa.V1.specs))
    monkeypatch.setattr(ff, "open_remote", lambda url: fake_remote(next(c for c in v1_collections if c in url), specs=fa.V1.specs))
    monkeypatch.setattr(ff, "remote_call", lambda fn, *args, **kwargs: fn(*args))

    def v1_cloud_copy(area, times, product):
        assert product is fa.V1
        return {  # like the real v1 Zarr: no t or q
            s.name: fake_remote(s.collection, specs=fa.V1.specs)[s.source_name].isel(lev=0).sel(**ff._grid_selection(area.bbox))
            for s in product.specs if s.source_name not in ("t", "q")
        }

    monkeypatch.setattr(fa, "read_zarr", v1_cloud_copy)
    nc_path = fa.run(ATLANTA, WINDOW, tmp_path, workers=2, product=fa.V1)

    assert nc_path.name.startswith("geos_cf_v1_analysis_point_33.75N_84.39W_")
    ds = xr.open_dataset(nc_path)
    assert list(ds.data_vars) == [s.name for s in ff.VARIABLES]  # same columns as v2
    assert bool(ds["pm10"].isnull().all()) and "not available" in ds["pm10"].attrs["comment"]
    assert ds["pm25"].attrs["source_name"] == "pm25_rh35_gcc" and ds["pm25"].attrs["units"] == "ug m-3"
    assert not bool(ds.drop_vars("pm10").to_array().isnull().any())
    assert ds.attrs["source_version"] == "v1" and ds.attrs["variables_not_available"] == "pm10"
    assert (tmp_path / ".cache" / "analysis_v1_point_33.75N_84.39W").is_dir()
