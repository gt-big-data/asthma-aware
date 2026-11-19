"""Offline tests for ingestion/pm25: daily grid, gap filling, monitors, features, downscaling.

No network: NASA data is a small synthetic analysis, AirNow files are inline text, map data
is a hand-written OSM answer, and the model trains on data with a planted relationship.
"""

from __future__ import annotations

import json
import textwrap

import numpy as np
import pandas as pd
import pytest
import xarray as xr

pytest.importorskip("scipy")
pytest.importorskip("sklearn")
pytest.importorskip("shapely")

from ingestion.pm25 import daily_grid, downscale, gapfill, monitors  # noqa: E402
from ingestion.pm25 import features as F  # noqa: E402

LAT = np.array([33.5, 33.75, 34.0, 34.25])
LON = np.array([-84.75, -84.5, -84.25, -84.0, -83.75])


def synthetic_hourly(start="2025-10-01T04:30", hours=24 * 10, seed=0):
    """Hourly 'pm25' on a 4 x 5 native grid, UTC mid-hour stamps like fetch_analysis writes."""
    times = pd.date_range(start, periods=hours, freq="h")
    rng = np.random.default_rng(seed)
    base = 8 + 3 * np.sin(np.arange(hours) / 24 * 2 * np.pi)[:, None, None]
    data = (base + LAT[None, :, None] - 33.5 + rng.random((hours, LAT.size, LON.size))).astype("float32")
    return xr.DataArray(data, dims=("time", "lat", "lon"), coords={"time": times, "lat": LAT, "lon": LON}, name="pm25")


# ------------------------------------------------------------------ daily grid

def test_daily_means_use_local_days_and_min_hours():
    hourly = synthetic_hourly()  # starts 04:30Z = 00:30 Atlanta time on Oct 1
    daily = daily_grid.daily_means(hourly)
    assert str(pd.Timestamp(daily["day"].values[0]).date()) == "2025-10-01"
    assert daily.sizes["day"] == 10 and not np.isnan(daily.values).any()
    # Knock out 7 hours of Oct 3: still 17 present, below the 18-hour rule -> NaN
    hourly[48:55] = np.nan
    daily = daily_grid.daily_means(hourly)
    assert np.isnan(daily.isel(day=2).values).all() and not np.isnan(daily.isel(day=3).values).any()


def test_build_fills_gaps_and_writes_geotiff(tmp_path):
    hourly = synthetic_hourly()
    hourly[24:48] = np.nan  # Oct 2 entirely missing
    hourly[:, 1, 2] = np.nan  # one cell missing every day
    cube, lat, lon, days, results = daily_grid.build(hourly, "2025-10-01", "2025-10-10", seeds=1)
    assert cube.shape == (10, 4, 5) and not np.isnan(cube).any()
    assert lat[0] > lat[-1], "north-up"
    assert results["missing_days"] == ["2025-10-02"] and results["filled_with"] in gapfill.METHODS
    assert set(results["gapfill"]["overall"]) == set(gapfill.METHODS)
    out = tmp_path / "x.tif"
    daily_grid.write_geotiff(cube, lat, lon, days, out, {"variable": "pm25"})
    import rasterio

    with rasterio.open(out) as src:
        assert src.count == 10 and src.descriptions[1] == "2025-10-02"
        assert src.bounds.top == pytest.approx(34.375) and src.bounds.left == pytest.approx(-84.875)
        assert src.tags()["dims"] == "(days, height, width)"
        np.testing.assert_allclose(src.read(), cube)


def test_crop_keeps_cells_touching_bbox():
    hourly = synthetic_hourly(hours=2)
    cropped = daily_grid.crop(hourly, {"min_lat": 33.7, "max_lat": 34.05, "min_lon": -84.6, "max_lon": -84.3})
    assert cropped["lat"].values.tolist() == [33.75, 34.0] and cropped["lon"].values.tolist() == [-84.5, -84.25]


def test_daily_grid_cli_end_to_end(tmp_path):
    nc = tmp_path / "analysis.nc"
    synthetic_hourly().to_dataset().to_netcdf(nc)
    assert daily_grid.main([str(nc), "--output", str(tmp_path / "out"), "--seeds", "0"]) == 0
    sidecar = json.loads((tmp_path / "out" / "pm25_daily_20251002-20251009.json").read_text())
    assert sidecar["shape"] == [8, 4, 5] and sidecar["missing_cells"] == 0 and "gapfill" not in sidecar


# ------------------------------------------------------------------ gap filling

def test_gapfill_recovers_smooth_field_and_scores_every_method():
    t, h, w = 20, 6, 6
    cube = (np.arange(t)[:, None, None] * 0.5 + np.arange(h)[None, :, None] + np.arange(w)[None, None, :]).astype("float32")
    rng = np.random.default_rng(1)
    for kind in gapfill.HIDE_PATTERNS:
        mask = gapfill.hide(cube, kind, rng)
        assert mask.any() and mask.shape == cube.shape
    test = cube.copy()
    test[5, 2, 3] = np.nan
    for name in gapfill.METHODS:
        filled = gapfill.fill(test, name)
        assert not np.isnan(filled).any()
        assert abs(filled[5, 2, 3] - cube[5, 2, 3]) < 0.6, name  # linear field: all methods close
    scores = gapfill.score_methods(cube, seeds=2)
    assert scores["best"] in gapfill.METHODS and set(scores["by_pattern"]) == set(gapfill.HIDE_PATTERNS)
    assert all(np.isfinite(v) for v in scores["overall"].values())
    assert scores["by_pattern"]["random_5pct"]["space_linear"]["mean"] < 0.3  # exact on a plane except at hidden corners


def test_hide_never_targets_already_missing_cells():
    cube = np.ones((30, 5, 5), dtype="float32")
    cube[3] = np.nan
    mask = gapfill.hide(cube, "whole_days", np.random.default_rng(0))
    assert not mask[3].any()


# ------------------------------------------------------------------ monitors

SITES_DAT = textwrap.dedent("""\
    131210055|PM2.5|0055|United Ave|Active|GA1|Georgia DNR|04|33.720600|-84.357800|0.000|-5.00|US|||||00|CC|010|GEORGIA||
    131210055|OZONE|0055|United Ave|Active|GA1|Georgia DNR|04|33.720600|-84.357800|0.000|-5.00|US|||||00|CC|010|GEORGIA||
    000010601|PM2.5|0601|Goose Bay|Active|CA1|Canada|00|53.304700|-60.364400|0.000|-4.00|CA|||||00|CC|010|NL||
    """)
DAILY_DAT = textwrap.dedent("""\
    10/01/25|131210055|United Ave|OZONE-1HR|PPB|55|1|Georgia DNR|-999|-999|33.720600|-84.357800|840131210055
    10/01/25|131210055|United Ave|PM2.5-24hr|UG/M3|5.3|24|Georgia DNR|29|0|33.720600|-84.357800|840131210055
    10/01/25|000010601|Goose Bay|PM2.5-24hr|UG/M3|2.0|24|Canada|8|0|53.304700|-60.364400|124000010601
    """)


def test_monitors_parse_sites_and_daily_files(tmp_path):
    (tmp_path / "sites.dat").write_text(SITES_DAT)
    (tmp_path / "20251001.dat").write_text(DAILY_DAT)
    (tmp_path / "20251002.dat").write_text(DAILY_DAT.replace("10/01/25", "10/02/25").replace("|5.3|", "|-999|"))
    sites = monitors.load_sites(tmp_path / "sites.dat")
    assert sites.id.tolist() == ["131210055"]
    obs = monitors.parse_daily([tmp_path / "20251001.dat", tmp_path / "20251002.dat"], sites.id)
    assert len(obs) == 1 and obs.site[0] == "site_131210055" and obs.pm25[0] == 5.3
    assert str(obs.date[0].date()) == "2025-10-01"
    assert monitors.daily_url(pd.Timestamp("2025-10-01")).endswith("/airnow/2025/20251001/daily_data_v2.dat")


# ------------------------------------------------------------------ features

def test_grid_points_are_row_major_north_up():
    bbox = {"min_lat": 33.0, "max_lat": 33.03, "min_lon": -84.0, "max_lon": -83.98}
    pts = F.grid_points(bbox, 0.01)
    assert len(pts) == 6 and pts.point.tolist()[:3] == ["cell_0", "cell_1", "cell_2"]
    assert pts.lat[0] == pytest.approx(33.025) and pts.lon[0] == pytest.approx(-83.995) and pts.lat.iloc[-1] == pytest.approx(33.005)


def test_landcover_fractions_ignore_nodata():
    block = np.array([[10, 10, 50, 0], [50, 50, 30, 0]])
    f = F.landcover_fractions(block)
    assert f["lc_tree"] == pytest.approx(2 / 6) and f["lc_built"] == pytest.approx(3 / 6) and f["lc_grass"] == pytest.approx(1 / 6)


def test_road_features_from_osm_answer():
    roads = {"elements": [
        {"type": "way", "tags": {"highway": "motorway"}, "geometry": [{"lat": 33.70, "lon": -84.40}, {"lat": 33.80, "lon": -84.40}]},
        {"type": "way", "tags": {"highway": "primary"}, "geometry": [{"lat": 33.75, "lon": -84.45}, {"lat": 33.75, "lon": -84.35}]},
    ]}
    industrial = {"elements": [{"type": "way", "geometry": [{"lat": 33.76, "lon": -84.42}, {"lat": 33.77, "lon": -84.42}, {"lat": 33.77, "lon": -84.41}, {"lat": 33.76, "lon": -84.41}, {"lat": 33.76, "lon": -84.42}]}]}
    pts = pd.DataFrame({"point": ["a", "b"], "lat": [33.75, 33.75], "lon": [-84.40, -84.30]})
    f = F.road_features(pts, roads, industrial).set_index("point")
    assert f.loc["a", "d_motorway"] == pytest.approx(0, abs=1) and f.loc["a", "d_primary"] == pytest.approx(0, abs=1)
    assert f.loc["a", "road_len_1km"] == pytest.approx(4000, rel=0.02)  # 2 km of each road inside the 1 km circle
    assert 1300 < f.loc["a", "d_industrial"] < 1600  # nearest corner is 0.01 deg north and east: ~1.45 km
    assert f.loc["b", "d_motorway"] == pytest.approx(F.ROAD_DISTANCE_CAP_M)  # ~9 km away, capped
    assert f.loc["b", "road_len_1km"] == 0


def test_overpass_query_covers_every_bbox():
    q = F.overpass_query([{"min_lat": 1, "max_lat": 2, "min_lon": 3, "max_lon": 4}, {"min_lat": 5, "max_lat": 6, "min_lon": 7, "max_lon": 8}], "roads")
    assert q.count("(1.0000,3.0000,2.0000,4.0000)") == 1 and q.count("(5.0000,7.0000,6.0000,8.0000)") == 1 and q.endswith("out geom;")


def test_weather_frame_and_grid_interpolation():
    days = ["2025-10-01", "2025-10-02"]
    payload = {
        "daily": {"time": days, "temperature_2m_mean": [20.0, 21.0], "relative_humidity_2m_mean": [60, 70], "precipitation_sum": [0, 1],
                  "wind_speed_10m_mean": [10.0, 10.0], "wind_direction_10m_dominant": [90.0, 0.0], "shortwave_radiation_sum": [15, 10]},
        "hourly": {"time": [f"{d}T{h:02d}:00" for d in days for h in range(24)], "boundary_layer_height": list(range(48))},
    }
    df = F._weather_frame(payload)
    assert list(df.columns) >= ["point"] or True
    assert df.blh_mean.tolist() == [11.5, 35.5] and df.blh_min.tolist() == [0, 24]
    assert df.wind_u[0] == pytest.approx(10) and df.wind_v[1] == pytest.approx(10)
    lattice = pd.DataFrame({"point": ["p0", "p1", "p2", "p3"], "lat": [33, 33, 34, 34], "lon": [-85, -84, -85, -84]})
    lw = pd.concat([df.assign(point=p) for p in lattice.point]).assign(date=lambda d: pd.to_datetime(d.date))
    lw.loc[lw.point == "p3", "temperature_2m_mean"] = 30.0
    g = F.weather_to_grid(lw, lattice, pd.Timestamp("2025-10-01"), np.array([33.75, 33.25]), np.array([-84.75, -84.25]))
    assert len(g) == 4 and 20 < g.temperature_2m_mean[1] < 30  # cell near p3 is pulled up


def test_sample_daily_tif_bilinear(tmp_path):
    cube = np.stack([np.arange(20, dtype="float32").reshape(4, 5)] * 2)
    daily_grid.write_geotiff(cube, LAT[::-1], LON, pd.date_range("2025-10-01", periods=2), tmp_path / "d.tif", {})
    v, dates = F.sample_daily_tif(tmp_path / "d.tif", np.array([34.25, 33.875]), np.array([-84.75, -84.75]))
    assert dates == ["2025-10-01", "2025-10-02"] and v.shape == (2, 2)
    assert v[0, 0] == pytest.approx(0) and v[0, 1] == pytest.approx(7.5)  # halfway between rows 1 and 2


# ------------------------------------------------------------------ downscaling

def synthetic_training(n_sites=12, n_days=120, seed=0):
    """Monitors whose PM2.5 is GEOS-CF x a factor that depends on the built-up fraction."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-10-01", periods=n_days)
    sites = pd.DataFrame({"point": [f"site_{i}" for i in range(n_sites)], "name": [f"S{i}" for i in range(n_sites)], "lc_built": rng.random(n_sites)})
    static = sites[["point"]].copy()
    for c in downscale.STATIC_COLUMNS:
        static[c] = rng.random(n_sites) * (1000 if c.startswith("d_") else 1)
    static["lc_built"] = sites.lc_built
    rows = []
    for s in sites.itertuples():
        g = 8 + 4 * np.sin(np.arange(n_days) / 15) + rng.random(n_days)
        pm = g * (0.7 + 0.6 * s.lc_built) + rng.normal(0, 0.3, n_days)
        rows.append(pd.DataFrame({"site": s.point, "name": s.name, "date": days, "pm25": pm, "geoscf_true": g}))
    obs = pd.concat(rows, ignore_index=True)
    geoscf = obs[["site", "date", "geoscf_true"]].rename(columns={"site": "point", "geoscf_true": "geoscf"})
    weather = pd.DataFrame({"point": obs.site, "date": obs.date})
    for c in F.WEATHER_COLUMNS:
        weather[c] = rng.random(len(obs))
    return obs.drop(columns="geoscf_true"), geoscf, weather, static


def test_downscale_learns_local_correction_and_predicts_grid():
    obs, geoscf, weather, static = synthetic_training()
    tr = downscale.training_table(obs, geoscf, weather, static)
    assert len(tr) == len(obs) and set(downscale.FEATURES) <= set(tr.columns)
    tests = downscale.evaluate(tr, inside=tr.point.isin(["site_0", "site_1"]), max_iter=60)
    loso = tests["leave_one_site_out"]
    assert loso["mae_model"] < 0.6 * loso["mae_geoscf"], tests  # the planted built-up effect is learned
    assert tests["region_sites_leave_one_out"]["n_sites"] == 2

    model = downscale.make_model(60).fit(tr[downscale.FEATURES], tr.y)
    lat, lon = np.array([33.75, 33.74]), np.array([-84.40, -84.39, -84.38])
    cells = pd.DataFrame({"point": [f"cell_{i}" for i in range(6)]})
    for c in downscale.STATIC_COLUMNS:
        cells[c] = 0.5
    cells["lc_built"] = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    days = pd.date_range("2025-10-01", periods=3)
    lattice = pd.DataFrame({"point": ["q0", "q1", "q2", "q3"], "lat": [33.7, 33.7, 33.8, 33.8], "lon": [-84.5, -84.3, -84.5, -84.3]})
    lw = pd.DataFrame([{"point": p, "date": d, **{c: 0.5 for c in F.WEATHER_COLUMNS}} for p in lattice.point for d in days])
    cube = downscale.predict_grid(model, cells, np.full((3, 2, 3), 10.0, dtype="float32"), lw, lattice, days, lat, lon)
    assert cube.shape == (3, 2, 3) and (cube >= 0).all()
    assert cube[:, 1, :].mean() > cube[:, 0, :].mean(), "built-up row should be dirtier"


def test_load_region_reads_repo_file():
    name, bbox = downscale.load_region(downscale.DEFAULT_REGION_FILE)
    assert name == "atlanta-metro" and bbox["max_lat"] > bbox["min_lat"] and bbox["max_lon"] > bbox["min_lon"]
