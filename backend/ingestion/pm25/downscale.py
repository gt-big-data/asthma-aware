"""Downscale the daily 25 km GEOS-CF PM2.5 grid to ~1 km over a region.

How it works: at every regulatory monitor and day we know the real PM2.5 and the GEOS-CF
value, plus the clues in ``features`` (land cover, roads, elevation, weather). A gradient-
boosted model learns log(monitor / GEOS-CF) from the clues, so GEOS-CF keeps the regional
day-to-day signal and the model supplies the local correction. It is then applied to every
grid cell and day. Tests hide whole monitors and whole months before scoring.

Usage (from backend/), after ``daily_grid`` and ``monitors``:
    python -m ingestion.pm25.downscale --daily data/pm25/pm25_daily_20251001-20260930.tif \\
        --monitors data/pm25/monitors --output data/pm25

Outputs: pm25_1km_<region>_<start>-<end>.tif (days x height x width), results.json, and a
cache/ folder with every downloaded clue so a rerun is quick.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ingestion.geos_cf import fetch_forecast as ff

from . import features as F
from . import monitors as M
from .daily_grid import DEFAULT_TZ, write_geotiff

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGION_FILE = PROJECT_ROOT / "ingestion" / "regions" / "atlanta-metro.json"
DEFAULT_RES_DEG = 0.01  # ~1 km
STATIC_COLUMNS = F.LANDCOVER_COLUMNS + ["elevation"] + F.ROAD_COLUMNS
CALENDAR_COLUMNS = ["doy_sin", "doy_cos", "weekday"]
FEATURES = ["geoscf"] + F.WEATHER_COLUMNS + STATIC_COLUMNS + CALENDAR_COLUMNS


def load_region(path: Path) -> Tuple[str, Dict[str, float]]:
    data = json.loads(Path(path).read_text())
    bbox = {k: float(data[k]) for k in ("min_lat", "max_lat", "min_lon", "max_lon")}
    return str(data.get("region", path.stem)), bbox


def make_model(max_iter: int = 400):
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(max_iter=max_iter, learning_rate=0.05, max_depth=6, min_samples_leaf=40, l2_regularization=1.0, random_state=0)


def prepare_static(static: pd.DataFrame) -> pd.DataFrame:
    """Log-compress the distances; they matter near zero and not at all past a few km."""
    static = static.copy()
    for c in ("d_motorway", "d_primary", "d_industrial"):
        static[c] = np.log1p(np.minimum(static[c], F.ROAD_DISTANCE_CAP_M))
    return static


def training_table(obs: pd.DataFrame, geoscf: pd.DataFrame, weather: pd.DataFrame, static: pd.DataFrame) -> pd.DataFrame:
    """Join monitor readings with their clues. Columns: FEATURES, pm25, y, point, name, date."""
    tr = (obs.rename(columns={"site": "point"}).merge(geoscf, on=["point", "date"]).merge(weather, on=["point", "date"])
          .merge(prepare_static(static), on="point"))
    tr = tr[(tr.pm25 > 0) & (tr.geoscf > 0)].copy()
    tr = F.calendar(tr)
    tr["y"] = np.log(tr.pm25 / tr.geoscf)
    return tr.reset_index(drop=True)


def _mae(a, b) -> float:
    return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))


def _held_out(tr: pd.DataFrame, groups: pd.Series, max_iter: int) -> np.ndarray:
    """Predict every row with a model that never saw its group."""
    pred = np.empty(len(tr))
    for g in groups.unique():
        m = (groups == g).values
        model = make_model(max_iter).fit(tr.loc[~m, FEATURES], tr.loc[~m, "y"])
        pred[m] = np.exp(model.predict(tr.loc[m, FEATURES])) * tr.loc[m, "geoscf"]
    return pred


def evaluate(tr: pd.DataFrame, inside: Optional[pd.Series] = None, max_iter: int = 400) -> Dict[str, object]:
    """Hidden-monitor and hidden-month MAE for the model and for GEOS-CF alone (the baseline)."""
    by_site = _held_out(tr, tr.point, max_iter)
    by_month = _held_out(tr, tr.date.dt.to_period("M"), max_iter)
    per_site = {}
    for s in tr.point.unique():
        m = (tr.point == s).values
        per_site[str(tr.loc[m, "name"].iloc[0])] = {
            "n": int(m.sum()), "mae_model": _mae(by_site[m], tr.pm25[m]), "mae_geoscf": _mae(tr.geoscf[m], tr.pm25[m]),
            "bias_model": float(np.mean(by_site[m] - tr.pm25[m])), "bias_geoscf": float(np.mean(tr.geoscf[m] - tr.pm25[m])),
        }
    out: Dict[str, object] = {
        "leave_one_site_out": {"mae_model": _mae(by_site, tr.pm25), "mae_geoscf": _mae(tr.geoscf, tr.pm25),
                               "r2_model": float(np.corrcoef(by_site, tr.pm25)[0, 1] ** 2), "r2_geoscf": float(np.corrcoef(tr.geoscf, tr.pm25)[0, 1] ** 2),
                               "per_site": per_site},
        "leave_one_month_out": {"mae_model": _mae(by_month, tr.pm25), "mae_geoscf": _mae(tr.geoscf, tr.pm25)},
    }
    if inside is not None and inside.any():
        m = inside.values
        out["region_sites_leave_one_out"] = {"n_sites": int(tr.point[m].nunique()), "mae_model": _mae(by_site[m], tr.pm25[m]), "mae_geoscf": _mae(tr.geoscf[m], tr.pm25[m])}
    return out


def predict_grid(model, static_cells: pd.DataFrame, geoscf_grid: np.ndarray, lattice_weather: pd.DataFrame, lattice: pd.DataFrame,
                 days: pd.DatetimeIndex, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """(days, H, W) predictions. static_cells must be in row-major cell order."""
    H, W = len(lat), len(lon)
    static = prepare_static(static_cells)[STATIC_COLUMNS].reset_index(drop=True)
    cube = np.zeros((len(days), H, W), dtype="float32")
    for d, day in enumerate(days):
        X = static.copy()
        wx = F.weather_to_grid(lattice_weather, lattice, day, lat, lon)
        for c in F.WEATHER_COLUMNS:
            X[c] = wx[c].values
        X["geoscf"] = geoscf_grid[d].ravel()
        X["date"] = day
        X = F.calendar(X)
        cube[d] = (np.exp(model.predict(X[FEATURES])) * X.geoscf.values).reshape(H, W)
    return np.clip(cube, 0, None)


# ---------------------------------------------------------------- feature assembly with caching

def _cached(path: Path, build, **read_kw) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path, **read_kw)
    df = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def assemble(daily_tif: Path, monitors_dir: Path, bbox: Dict[str, float], res: float, start: str, end: str, tz: str, cache: Path):
    """Everything the model needs, downloading each clue once into cache/."""
    days = pd.date_range(start, end, freq="D")
    obs = pd.read_csv(monitors_dir / "pm25_monitors.csv", parse_dates=["date"])
    obs = obs[obs.date.between(days[0], days[-1])]
    sites = pd.read_csv(monitors_dir / "sites.csv", dtype={"id": str})
    sites = sites[("site_" + sites.id).isin(obs.site)].reset_index(drop=True)
    pts_sites = F.site_points(sites)
    pts_cells = F.grid_points(bbox, res)
    lat, lon = F.grid_centers(bbox, res)
    logger.info("%d monitors with readings, grid %d x %d cells @ %.3f deg", len(sites), len(lat), len(lon), res)

    # GEOS-CF: sampled at sites, interpolated to cells
    g_sites, dates = F.sample_daily_tif(daily_tif, pts_sites.lat.values, pts_sites.lon.values)
    tif_days = pd.DatetimeIndex(pd.to_datetime(dates))
    keep = tif_days.get_indexer(days)
    if (keep < 0).any():
        raise ValueError(f"{daily_tif.name} lacks {int((keep < 0).sum())} of the requested days")
    geoscf = pd.DataFrame(g_sites[keep], index=days, columns=pts_sites.point).rename_axis("date").reset_index().melt(id_vars="date", var_name="point", value_name="geoscf")
    g_cells, _ = F.sample_daily_tif(daily_tif, pts_cells.lat.values, pts_cells.lon.values)
    geoscf_grid = g_cells[keep].reshape(len(days), len(lat), len(lon)).astype("float32")

    # static clues
    inside = sites.lat.between(bbox["min_lat"], bbox["max_lat"]) & sites.lon.between(bbox["min_lon"], bbox["max_lon"])
    patches = [bbox] + [{"min_lat": r.lat - 0.06, "max_lat": r.lat + 0.06, "min_lon": r.lon - 0.06, "max_lon": r.lon + 0.06} for r in sites[~inside].itertuples()]
    roads = F.fetch_osm(patches, "roads", cache / "osm_roads.json")
    industrial = F.fetch_osm(patches, "industrial", cache / "osm_industrial.json")
    static_sites = _cached(cache / "static_sites.csv", lambda: (
        F.landcover(pts_sites, res).merge(F.elevation(pts_sites, res), on="point").merge(F.road_features(pts_sites, roads, industrial), on="point")))
    static_cells = _cached(cache / "static_cells.csv", lambda: (
        F.landcover_grid(bbox, res).merge(F.elevation_grid(bbox, res), on="point").merge(F.road_features(pts_cells, roads, industrial), on="point")))
    static_cells["order"] = static_cells.point.str[5:].astype(int)
    static_cells = static_cells.sort_values("order").drop(columns="order").reset_index(drop=True)

    # weather
    lattice = F.weather_lattice(bbox)
    w_sites = _cached(cache / "weather_sites.csv", lambda: F.weather(pts_sites, start, end, tz), parse_dates=["date"])
    w_lattice = _cached(cache / "weather_lattice.csv", lambda: F.weather(lattice, start, end, tz), parse_dates=["date"])

    tr = training_table(obs, geoscf, w_sites, static_sites)
    tr_inside = tr.point.isin("site_" + sites[inside].id)
    return tr, tr_inside, static_cells, geoscf_grid, w_lattice, lattice, days, lat, lon


def run(daily_tif: Path, monitors_dir: Path, region_file: Path, res: float, start: str, end: str, tz: str, output: Path, max_iter: int = 400) -> Path:
    region, bbox = load_region(region_file)
    cache = output / "cache"
    tr, inside, static_cells, geoscf_grid, w_lattice, lattice, days, lat, lon = assemble(daily_tif, monitors_dir, bbox, res, start, end, tz, cache)
    logger.info("training rows %d from %d monitors over %d days", len(tr), tr.point.nunique(), tr.date.nunique())

    tests = evaluate(tr, inside, max_iter)
    for k, v in tests.items():
        logger.info("%-28s model MAE %.2f   GEOS-CF alone %.2f", k, v["mae_model"], v["mae_geoscf"])
    model = make_model(max_iter).fit(tr[FEATURES], tr.y)
    from sklearn.inspection import permutation_importance

    imp = permutation_importance(model, tr[FEATURES], tr.y, n_repeats=5, random_state=0).importances_mean
    importance = dict(sorted(zip(FEATURES, map(float, imp)), key=lambda kv: -kv[1]))

    cube = predict_grid(model, static_cells, geoscf_grid, w_lattice, lattice, days, lat, lon)
    stem = f"pm25_1km_{region}_{start.replace('-', '')}-{end.replace('-', '')}"
    tif = output / f"{stem}.tif"
    write_geotiff(cube, lat, lon, days, tif, {
        "variable": "pm25", "units": "ug m-3", "resolution_deg": str(res),
        "method": f"{ff.SOURCE} {ff.SOURCE_VERSION} analysis PM2.5 downscaled with gradient boosting trained on AirNow monitors",
        "source_file": daily_tif.name,
    })
    results = {
        "file": tif.name, "shape": list(cube.shape), "region": region, "bbox": bbox, "resolution_deg": res,
        "training_rows": int(len(tr)), "training_sites": int(tr.point.nunique()), "features": FEATURES, "tests": tests, "importance": importance,
        "stats": {"mean": float(cube.mean()), "min": float(cube.min()), "max": float(cube.max()), "p99": float(np.percentile(cube, 99)),
                  "within_day_spread_mean": float(np.mean(cube.max(axis=(1, 2)) - cube.min(axis=(1, 2))))},
    }
    (output / f"{stem}.json").write_text(json.dumps(results, indent=2) + "\n")
    logger.info("  output  %s", tif)
    return tif


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Downscale daily GEOS-CF PM2.5 to ~1 km using monitor readings and public map data.")
    parser.add_argument("--daily", type=Path, required=True, help="daily GeoTIFF from ingestion.pm25.daily_grid")
    parser.add_argument("--monitors", type=Path, default=Path("data/pm25/monitors"), help="folder written by ingestion.pm25.monitors")
    parser.add_argument("--region-file", type=Path, default=DEFAULT_REGION_FILE)
    parser.add_argument("--res", type=float, default=DEFAULT_RES_DEG, help="cell size in degrees (0.01 is ~1 km)")
    parser.add_argument("--start", help="first day (default: the daily file's first band)")
    parser.add_argument("--end", help="last day, inclusive (default: the daily file's last band)")
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument("--output", type=Path, default=Path("data/pm25"))
    parser.add_argument("--max-iter", type=int, default=400, help="boosting rounds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    import rasterio

    with rasterio.open(args.daily) as src:
        bands = list(src.descriptions)
    start, end = args.start or bands[0], args.end or bands[-1]
    try:
        run(args.daily, args.monitors, args.region_file, args.res, start, end, args.tz, args.output, args.max_iter)
    except (ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
