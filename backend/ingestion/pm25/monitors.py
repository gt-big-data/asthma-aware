"""Regulatory monitor PM2.5 from AirNow's public daily archive. No API key.

``files.airnowtech.org`` publishes one ``daily_data_v2.dat`` per day with every site's 24-hour
PM2.5, and a site list. This is the ground truth the downscaling model learns from (the
``air_quality`` collector uses the keyed API and only reaches recent hours).

Usage (from backend/):
    python -m ingestion.pm25.monitors --start 2025-10-01 --end 2026-09-30 --output data/pm25/monitors
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import httpx
import pandas as pd

from ingestion.geos_cf import fetch_forecast as ff

logger = logging.getLogger(__name__)

ARCHIVE = "https://files.airnowtech.org/airnow"
SITES_URL = f"{ARCHIVE}/today/monitoring_site_locations.dat"
PARAMETER = "PM2.5-24hr"
DEFAULT_BBOX = ff.GEORGIA_BBOX

SITE_COLUMNS = ["id", "param", "sid", "name", "status", "agency_id", "agency", "epa_region", "lat", "lon",
                "elev", "gmt", "country"] + [f"extra{i}" for i in range(11)]


def daily_url(day: pd.Timestamp) -> str:
    return f"{ARCHIVE}/{day:%Y}/{day:%Y%m%d}/daily_data_v2.dat"


def download(days: Iterable[pd.Timestamp], dest: Path, client: Optional[httpx.Client] = None) -> List[Path]:
    """Fetch each day's file (and the site list) into dest, skipping files already there."""
    dest.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    paths: List[Path] = []
    try:
        sites = dest / "monitoring_site_locations.dat"
        if not sites.exists():
            sites.write_bytes(client.get(SITES_URL).raise_for_status().content)
        for day in days:
            path = dest / f"{day:%Y%m%d}.dat"
            if not path.exists():
                r = client.get(daily_url(day))
                if r.status_code != 200:
                    logger.warning("%s: HTTP %d, skipped", day.date(), r.status_code)
                    continue
                path.write_bytes(r.content)
            paths.append(path)
    finally:
        if own:
            client.close()
    return paths


def load_sites(path: Path, bbox: Dict[str, float] = DEFAULT_BBOX) -> pd.DataFrame:
    """PM2.5 sites inside bbox: columns id, name, status, lat, lon."""
    s = pd.read_csv(path, sep="|", header=None, names=SITE_COLUMNS, encoding="latin-1", dtype=str, on_bad_lines="skip")
    s["lat"] = pd.to_numeric(s["lat"], errors="coerce")
    s["lon"] = pd.to_numeric(s["lon"], errors="coerce")
    s = s[(s["param"] == "PM2.5") & s.lat.between(bbox["min_lat"], bbox["max_lat"]) & s.lon.between(bbox["min_lon"], bbox["max_lon"])]
    return s[["id", "name", "status", "lat", "lon"]].drop_duplicates("id").reset_index(drop=True)


def parse_daily(paths: Iterable[Path], site_ids: Iterable[str]) -> pd.DataFrame:
    """One row per (site, date): columns site ('site_<id>'), name, date, pm25 (ug/m3)."""
    wanted = set(site_ids)
    frames = []
    for path in paths:
        d = pd.read_csv(path, sep="|", header=None, dtype=str, encoding="latin-1", on_bad_lines="skip")
        d = d[(d[3] == PARAMETER) & d[1].isin(wanted)]
        if d.empty:
            continue
        frames.append(pd.DataFrame({
            "site": "site_" + d[1], "name": d[2],
            "date": pd.to_datetime(d[0], format="%m/%d/%y"), "pm25": pd.to_numeric(d[5], errors="coerce"),
        }))
    if not frames:
        return pd.DataFrame(columns=["site", "name", "date", "pm25"])
    obs = pd.concat(frames, ignore_index=True)
    return obs[obs.pm25.notna() & (obs.pm25 >= 0)].reset_index(drop=True)


def collect(start: str, end: str, dest: Path, bbox: Dict[str, float] = DEFAULT_BBOX) -> pd.DataFrame:
    paths = download(pd.date_range(start, end, freq="D"), dest)
    sites = load_sites(dest / "monitoring_site_locations.dat", bbox)
    obs = parse_daily(paths, sites.id)
    obs.to_csv(dest / "pm25_monitors.csv", index=False)
    sites.to_csv(dest / "sites.csv", index=False)
    logger.info("%d readings from %d sites over %d days -> %s", len(obs), obs.site.nunique(), obs.date.nunique(), dest / "pm25_monitors.csv")
    return obs


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Download AirNow's public daily PM2.5 for regulatory monitors in Georgia.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="last day, inclusive")
    parser.add_argument("--bbox", type=lambda t: ff._floats(t, 4), metavar="MIN_LAT,MAX_LAT,MIN_LON,MAX_LON")
    parser.add_argument("--output", type=Path, default=Path("data/pm25/monitors"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bbox = DEFAULT_BBOX
    if args.bbox:
        bbox = dict(zip(("min_lat", "max_lat", "min_lon", "max_lon"), args.bbox))
    try:
        collect(args.start, args.end, args.output, bbox)
    except httpx.HTTPError as exc:
        logger.error("AirNow archive: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
