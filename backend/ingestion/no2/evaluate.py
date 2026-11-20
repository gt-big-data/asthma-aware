"""Score surface-NO2 estimates against regulatory monitors at the TROPOMI overpass.

For every day in the given GEOS-CF analysis files this matches, at each AirNow NO2 monitor:

* the monitor's mean over the overpass hours (default 18:00-20:00 UTC, about 13:30 local solar
  time over Georgia), from AirNow's public hourly archive (no key);
* GEOS-CF's surface NO2, column and mixing conditions in the monitor's 0.25 degree cell;
* optionally the TROPOMI column within 3 km of the monitor and its cloud fraction, via Earth
  Engine (needs a registered project: --ee-project or EE_PROJECT_ID).

and scores two estimates: `geos` (what the risk map uses today) and `tropomi_scaled`
(ingestion.no2.surface.column_to_surface). Scores are also split by boundary-layer height and,
with TROPOMI, by cloud fraction, the conditions Chang et al. (2025) found decide whether a
column says anything about the ground.

Usage (from backend/):
    python -m ingestion.no2.evaluate data/geos_cf/*first5days.nc --output data/no2/eval \\
        --ee-project my-ee-project
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import httpx
import numpy as np
import pandas as pd
import xarray as xr

from ingestion.no2 import surface

logger = logging.getLogger(__name__)

# AirNow's public archive (no key): hourly files for every site, plus a site list.
ARCHIVE = "https://files.airnowtech.org/airnow"
SITES_URL = f"{ARCHIVE}/today/monitoring_site_locations.dat"
SITE_COLUMNS = ["id", "param", "sid", "name", "status", "agency_id", "agency", "epa_region", "lat", "lon",
                "elev", "gmt", "country"] + [f"extra{i}" for i in range(11)]
PARAMETER = "NO2"
OVERPASS_HOURS = (18, 19)
HOURLY_COLUMNS = ["date", "time", "site", "name", "gmt_offset", "param", "units", "value", "agency"]
SAMPLE_RADIUS_M = 3000
TROPOMI_BATCH_DAYS = 5
ESTIMATES = ("geos", "tropomi_scaled")


def hourly_url(day: pd.Timestamp, hour: int) -> str:
    return f"{ARCHIVE}/{day:%Y}/{day:%Y%m%d}/HourlyData_{day:%Y%m%d}{hour:02d}.dat"


def load_sites(path: Path, bbox: Dict[str, float]) -> pd.DataFrame:
    """NO2 sites inside bbox: columns id, name, lat, lon."""
    s = pd.read_csv(path, sep="|", header=None, names=SITE_COLUMNS, encoding="latin-1", dtype=str,
                    on_bad_lines="skip")
    s["lat"] = pd.to_numeric(s["lat"], errors="coerce")
    s["lon"] = pd.to_numeric(s["lon"], errors="coerce")
    s = s[(s["param"] == PARAMETER) & s.lat.between(bbox["min_lat"], bbox["max_lat"])
          & s.lon.between(bbox["min_lon"], bbox["max_lon"])]
    return s[["id", "name", "lat", "lon"]].drop_duplicates("id").reset_index(drop=True)


def parse_hourly(content: bytes, site_ids: Iterable[str]) -> pd.DataFrame:
    """NO2 rows of one HourlyData file for the given sites: columns site, time (UTC), no2_ppb."""
    d = pd.read_csv(io.BytesIO(content), sep="|", header=None, names=HOURLY_COLUMNS, dtype=str,
                    encoding="latin-1", on_bad_lines="skip")
    d = d[(d.param == PARAMETER) & d.site.isin(set(site_ids))]
    value = pd.to_numeric(d.value, errors="coerce")
    out = pd.DataFrame({"site": d.site, "time": pd.to_datetime(d.date + " " + d.time, format="%m/%d/%y %H:%M"),
                        "no2_ppb": value})
    # AirNow allows small negative readings (instrument noise); below -0.5 ppb is invalid.
    return out[out.no2_ppb.notna() & (out.no2_ppb >= -0.5)].reset_index(drop=True)


def download_hourly(days: Iterable[pd.Timestamp], hours: Sequence[int], site_ids: Sequence[str], dest: Path,
                    client: httpx.Client) -> pd.DataFrame:
    """Every (day, hour) file, cached as a small CSV of just these sites' NO2."""
    dest.mkdir(parents=True, exist_ok=True)
    frames = []
    for day in days:
        for hour in hours:
            path = dest / f"no2_{day:%Y%m%d}{hour:02d}.csv"
            if not path.exists():
                r = client.get(hourly_url(day, hour))
                if r.status_code != 200:
                    logger.warning("%s %02d UTC: HTTP %d, skipped", day.date(), hour, r.status_code)
                    continue
                parse_hourly(r.content, site_ids).to_csv(path, index=False)
            frames.append(pd.read_csv(path, dtype={"site": str}, parse_dates=["time"]))
    if not frames:
        return pd.DataFrame(columns=["site", "time", "no2_ppb"])
    return pd.concat(frames, ignore_index=True)


def overpass_samples(ds: xr.Dataset, obs: pd.DataFrame, sites: pd.DataFrame,
                     hours: Sequence[int] = OVERPASS_HOURS) -> pd.DataFrame:
    """One row per site and day with every overpass hour present in GEOS-CF."""
    times = pd.DatetimeIndex(ds.time.values)
    wanted = [pd.Timedelta(hours=h, minutes=30) for h in hours]
    days = sorted({t.normalize() for t in times if all(t.normalize() + w in times for w in wanted)})
    obs = obs.assign(day=obs.time.dt.normalize())
    rows = []
    for day in days:
        cells = ds.sel(time=[day + w for w in wanted]).mean("time")
        day_obs = obs[(obs.day == day) & obs.time.dt.hour.isin(hours)]
        for site in sites.itertuples():
            cell = cells.sel(lat=site.lat, lon=site.lon, method="nearest")
            reading = day_obs.loc[day_obs.site == site.id, "no2_ppb"]
            rows.append({
                "date": day, "site": site.id, "name": site.name, "lat": site.lat, "lon": site.lon,
                "geos_version": ds.attrs.get("source_version", ""),
                "observed_ppb": reading.mean() if len(reading) == len(hours) else np.nan,
                "geos_ppb": float(cell.no2) * 1e9,
                "geos_column": float(cell.tropcol_no2),
                "pbl_height": float(cell.pbl_height),
                "relative_humidity": float(cell.relative_humidity),
            })
    return pd.DataFrame(rows)


def tropomi_at_sites(dates: Sequence[pd.Timestamp], sites: pd.DataFrame, project: str) -> pd.DataFrame:
    """Daily TROPOMI L3 column (1e15 molec cm-2) and cloud fraction averaged within 3 km of each site."""
    import ee

    from ingestion.satellite import client

    client.initialize(project)
    points = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([s.lon, s.lat]).buffer(SAMPLE_RADIUS_M), {"site": s.id})
        for s in sites.itertuples()
    ])
    bands = [client.S5P_NO2_BAND, "cloud_fraction"]
    # A fully masked image keeps days without any TROPOMI pass from failing ("no bands"); they come
    # back as missing instead.
    placeholder = ee.Image.constant([0, 0]).rename(bands).toFloat().updateMask(0)

    def per_day(date):
        date = ee.Date(date)
        image = (ee.ImageCollection(client.S5P_NO2_COLLECTION).filterDate(date, date.advance(1, "day"))
                 .filterBounds(points).select(bands).merge(ee.ImageCollection([placeholder])).mean())
        return image.reduceRegions(points, ee.Reducer.mean(), 1113.2).map(
            lambda f: f.set("date", date.format("YYYY-MM-dd")))

    features = []
    names = sorted({pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates})
    # Small batches with backoff: new (noncommercial) projects allow few concurrent aggregations.
    for start in range(0, len(names), TROPOMI_BATCH_DAYS):
        batch = ee.FeatureCollection(ee.List(names[start:start + TROPOMI_BATCH_DAYS]).map(per_day)).flatten()
        for attempt in range(6):
            try:
                features += [f["properties"] for f in batch.getInfo()["features"]]
                break
            except ee.EEException as exc:
                if "concurrent" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(2 ** attempt)
    out = pd.DataFrame(features, columns=["site", "date", *bands])
    out = out.rename(columns={client.S5P_NO2_BAND: "tropomi_column", "cloud_fraction": "cloud_fraction"})
    out["tropomi_column"] = pd.to_numeric(out.tropomi_column, errors="coerce") * surface.MOL_M2_TO_1E15_MOLEC_CM2
    out["cloud_fraction"] = pd.to_numeric(out.cloud_fraction, errors="coerce")
    out["date"] = pd.to_datetime(out.date)
    return out


def add_estimates(samples: pd.DataFrame) -> pd.DataFrame:
    samples = samples.copy()
    samples["geos"] = samples.geos_ppb
    if "tropomi_column" in samples:
        samples["tropomi_scaled"] = surface.column_to_surface(samples.tropomi_column, samples.geos_ppb,
                                                              samples.geos_column)
    return samples


def report(samples: pd.DataFrame) -> Dict[str, object]:
    """Scores for each estimate overall, per site, and by mixing tercile. Paired: every estimate is
    scored on the same rows, those where all of them exist."""
    present = [e for e in ESTIMATES if e in samples]
    rows = samples.dropna(subset=["observed_ppb", *present])

    def block(frame):
        return {e: surface.scores(frame.observed_ppb, frame[e]) for e in present}

    result: Dict[str, object] = {"estimates": present, "overall": block(rows), "by_site": {}, "by_condition": {}}
    for (site, name), frame in rows.groupby(["site", "name"]):
        result["by_site"][f"{site} {name}"] = block(frame)
    for column in ("pbl_height", "relative_humidity", "cloud_fraction"):
        if column not in rows or rows[column].nunique() < 3:
            continue
        terciles = pd.qcut(rows[column], 3, labels=["low", "mid", "high"])
        result["by_condition"][column] = {
            str(level): {"range": [float(frame[column].min()), float(frame[column].max())], **block(frame)}
            for level, frame in rows.groupby(terciles, observed=True)
        }
    return result


def run(geos_paths: Sequence[Path], output: Path, *, ee_project: Optional[str] = None,
        hours: Sequence[int] = OVERPASS_HOURS) -> Dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "airnow"
    frames: List[pd.DataFrame] = []
    with httpx.Client(timeout=60, follow_redirects=True) as http:
        sites_file = cache / "monitoring_site_locations.dat"
        if not sites_file.exists():
            cache.mkdir(parents=True, exist_ok=True)
            sites_file.write_bytes(http.get(SITES_URL).raise_for_status().content)
        for path in geos_paths:
            with xr.open_dataset(path) as ds:
                bbox = {"min_lat": float(ds.lat.min()), "max_lat": float(ds.lat.max()),
                        "min_lon": float(ds.lon.min()), "max_lon": float(ds.lon.max())}
                sites = load_sites(sites_file, bbox)
                days = sorted({pd.Timestamp(t).normalize() for t in ds.time.values})
                obs = download_hourly(days, hours, list(sites.id), cache, http)
                frames.append(overpass_samples(ds, obs, sites, hours))
                logger.info("%s: %d site-days at %d sites", path.name, len(frames[-1]), len(sites))
    samples = pd.concat(frames, ignore_index=True)
    if ee_project:
        unique_sites = samples[["site", "lat", "lon"]].drop_duplicates("site").rename(columns={"site": "id"})
        samples = samples.merge(tropomi_at_sites(samples.date.unique(), unique_sites, ee_project),
                                on=["site", "date"], how="left")
    else:
        logger.warning("No Earth Engine project: scoring GEOS-CF only (pass --ee-project for TROPOMI)")
    samples = add_estimates(samples)
    samples.to_csv(output / "no2_overpass_samples.csv", index=False)
    result = report(samples)
    (output / "no2_overpass_scores.json").write_text(json.dumps(result, indent=2))
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)  # EE_PROJECT_ID
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("geos", nargs="+", type=Path, help="GEOS-CF analysis NetCDF(s) from fetch_analysis")
    parser.add_argument("--output", type=Path, default=Path("data/no2/eval"))
    parser.add_argument("--ee-project", default=os.environ.get("EE_PROJECT_ID") or None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    result = run(args.geos, args.output, ee_project=args.ee_project)
    for name, s in result["overall"].items():
        logger.info("%-15s n=%d R=%.2f bias=%+.2f ppb MAE=%.2f ppb observed/estimate=%.2f",
                    name, s["n"], s["r"], s["bias"], s["mae"], s["median_ratio"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
