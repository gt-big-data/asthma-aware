"""Offline tests for ingestion/no2: column-to-surface conversion and monitor scoring. No network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from ingestion.no2 import evaluate, surface


def test_unit_conversion_matches_avogadro():
    # 1e-4 mol m-2 = 6.022e19 molec m-2 = 6.022e15 molec cm-2
    assert 1e-4 * surface.MOL_M2_TO_1E15_MOLEC_CM2 == pytest.approx(6.02214076)


def test_column_to_surface_scales_geos_by_the_satellite_column():
    # Satellite sees twice GEOS-CF's column: keep GEOS-CF's profile shape, double the amount.
    assert surface.column_to_surface(8.0, 3.0, 4.0) == pytest.approx(6.0)


def test_scale_is_clamped_and_ignores_columns_at_the_noise_floor():
    assert surface.column_scale(400.0, 4.0) == pytest.approx(surface.SCALE_LIMITS[1])
    assert surface.column_scale(1.0, 100.0) == pytest.approx(surface.SCALE_LIMITS[0])
    assert surface.column_scale(0.1, 4.0) == 1.0  # satellite near zero: no information
    assert surface.column_scale(4.0, 0.1) == 1.0  # model column near zero: ratio unstable


def test_missing_satellite_pixel_stays_missing():
    out = surface.column_to_surface(np.array([np.nan, 4.0]), np.array([2.0, 2.0]), np.array([4.0, 4.0]))
    assert np.isnan(out[0]) and out[1] == pytest.approx(2.0)


def test_scores_drop_invalid_pairs():
    s = surface.scores([1, 2, 3, np.nan, 5], [2, 4, 6, 1, np.nan])
    assert s["n"] == 3 and s["r"] == pytest.approx(1.0) and s["bias"] == pytest.approx(2.0)
    assert s["median_ratio"] == pytest.approx(0.5)
    assert np.isnan(surface.scores([1], [1])["r"])


SITES = (
    "060000001|NO2|0001|Inside|Active|GA1|Agency|R4|33.75|-84.40|300|-5|US\n"
    "060000002|PM2.5|0002|Wrong param|Active|GA1|Agency|R4|33.75|-84.40|300|-5|US\n"
    "060000003|NO2|0003|Outside|Active|GA1|Agency|R4|40.00|-84.40|300|-5|US\n"
)


def test_load_sites_keeps_no2_sites_in_bbox(tmp_path):
    path = tmp_path / "sites.dat"
    path.write_text(SITES)
    sites = evaluate.load_sites(path, {"min_lat": 33, "max_lat": 35, "min_lon": -85, "max_lon": -83})
    assert list(sites.id) == ["060000001"]


def test_parse_hourly_filters_param_site_and_invalid_values():
    content = (
        "10/01/25|18:00|060000001|Inside|-4|NO2|PPB|7.5|Agency\n"
        "10/01/25|18:00|060000001|Inside|-4|NO|PPB|3.0|Agency\n"
        "10/01/25|18:00|060000009|Other|-4|NO2|PPB|9.0|Agency\n"
        "10/01/25|18:00|060000001|Inside|-4|NO2|PPB|-999|Agency\n"
    ).encode()
    out = evaluate.parse_hourly(content, ["060000001"])
    assert out.no2_ppb.tolist() == [7.5]
    assert out.time.iloc[0] == pd.Timestamp("2025-10-01T18:00")


def synthetic_geos(days=3):
    times = pd.date_range("2025-10-01T00:30", periods=24 * days, freq="h")
    lat, lon = np.array([33.5, 33.75, 34.0]), np.array([-84.5, -84.25])
    shape = (len(times), lat.size, lon.size)
    data = {
        "no2": np.full(shape, 4e-9), "tropcol_no2": np.full(shape, 5.0),
        "pbl_height": np.full(shape, 1200.0), "relative_humidity": np.full(shape, 0.5),
    }
    ds = xr.Dataset({k: (("time", "lat", "lon"), v) for k, v in data.items()},
                    coords={"time": times, "lat": lat, "lon": lon})
    ds.attrs["source_version"] = "v2"
    return ds


def test_overpass_samples_need_every_overpass_hour():
    ds = synthetic_geos()
    sites = pd.DataFrame({"id": ["s1"], "name": ["Site"], "lat": [33.76], "lon": [-84.30]})
    obs = pd.DataFrame({
        "site": ["s1", "s1", "s1"],
        "time": pd.to_datetime(["2025-10-01T18:00", "2025-10-01T19:00", "2025-10-02T18:00"]),
        "no2_ppb": [6.0, 10.0, 5.0],
    })
    samples = evaluate.overpass_samples(ds, obs, sites)
    assert len(samples) == 3
    assert samples.observed_ppb.iloc[0] == pytest.approx(8.0)
    assert np.isnan(samples.observed_ppb.iloc[1])  # day 2 is missing the 19:00 reading
    assert samples.geos_ppb.iloc[0] == pytest.approx(4.0)


def test_report_scores_estimates_on_the_same_rows():
    rng = np.random.default_rng(0)
    n = 30
    truth = rng.uniform(2, 20, n)
    samples = pd.DataFrame({
        "site": ["a"] * 15 + ["b"] * 15, "name": ["A"] * 15 + ["B"] * 15,
        "observed_ppb": truth, "geos_ppb": truth / 3, "geos_column": np.full(n, 5.0),
        "tropomi_column": np.where(np.arange(n) % 5 == 0, np.nan, 15.0),
        "pbl_height": rng.uniform(300, 2500, n), "relative_humidity": rng.uniform(0.2, 0.9, n),
        "cloud_fraction": rng.uniform(0, 0.3, n),
    })
    result = evaluate.report(evaluate.add_estimates(samples))
    overall = result["overall"]
    assert overall["geos"]["n"] == overall["tropomi_scaled"]["n"] == 24  # cloudy rows dropped for both
    assert overall["geos"]["median_ratio"] == pytest.approx(3.0)
    assert overall["tropomi_scaled"]["median_ratio"] == pytest.approx(1.0)
    assert set(result["by_condition"]) == {"pbl_height", "relative_humidity", "cloud_fraction"}
    assert set(result["by_site"]) == {"a A", "b B"}
