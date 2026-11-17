"""The 1 km "clues" the downscaling model uses, for monitor sites and for every grid cell.

Static (one value per place):
  * land cover fractions   -- ESA WorldCover 2021 (10 m), public cloud tiles
  * elevation              -- Copernicus DEM (30 m), public cloud tiles
  * roads and industry     -- OpenStreetMap via Overpass: distance to motorway/trunk, to a
                              primary road, to an industrial area; major-road length within 1 km
Daily (one value per place and day):
  * weather                -- ERA5 via Open-Meteo's archive: temperature, humidity, rain, wind,
                              sunshine, boundary-layer (mixing) height
  * GEOS-CF PM2.5          -- the daily 25 km grid from ``daily_grid``, bilinear to the point

Every source is public and keyless. Results are plain DataFrames keyed by a 'point' id
('site_<id>' or 'cell_<n>'), so they cache well as CSV.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from scipy.interpolate import griddata
from scipy.ndimage import map_coordinates

logger = logging.getLogger(__name__)

USER_AGENT = "asthma-aware-research/1.0"
WORLDCOVER = "/vsis3/esa-worldcover/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
COPERNICUS_DEM = "/vsis3/copernicus-dem-30m/{tile}/{tile}.tif"
OVERPASS = "https://overpass-api.de/api/interpreter"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

LANDCOVER_CLASSES = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare", 80: "water", 90: "wetland"}
LANDCOVER_COLUMNS = [f"lc_{n}" for n in LANDCOVER_CLASSES.values()]
ROAD_COLUMNS = ["d_motorway", "d_primary", "road_len_1km", "d_industrial"]
ROAD_DISTANCE_CAP_M = 5000.0  # beyond this a road's effect is nil, and outlying sites only have roads mapped nearby
WEATHER_DAILY = ["temperature_2m_mean", "relative_humidity_2m_mean", "precipitation_sum", "wind_speed_10m_mean",
                 "wind_direction_10m_dominant", "shortwave_radiation_sum"]
WEATHER_COLUMNS = ["temperature_2m_mean", "relative_humidity_2m_mean", "precipitation_sum", "wind_speed_10m_mean",
                   "shortwave_radiation_sum", "blh_mean", "blh_min", "wind_u", "wind_v"]
WEATHER_LATTICE_DEG = 0.1  # ERA5 is ~25 km; a 0.1 deg lattice over the region is plenty

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")


# ---------------------------------------------------------------- grids and points

def grid_centers(bbox: Dict[str, float], res: float) -> Tuple[np.ndarray, np.ndarray]:
    """Cell-centre latitudes (north to south) and longitudes (west to east)."""
    lat = np.arange(bbox["max_lat"] - res / 2, bbox["min_lat"], -res)
    lon = np.arange(bbox["min_lon"] + res / 2, bbox["max_lon"], res)
    return lat.round(6), lon.round(6)


def grid_points(bbox: Dict[str, float], res: float) -> pd.DataFrame:
    """Every cell as a point: columns point ('cell_<row*W+col>'), lat, lon, in row-major order."""
    lat, lon = grid_centers(bbox, res)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    return pd.DataFrame({"point": [f"cell_{i}" for i in range(la.size)], "lat": la.ravel(), "lon": lo.ravel()})


def site_points(sites: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"point": "site_" + sites.id.astype(str), "lat": sites.lat.values, "lon": sites.lon.values})


# ---------------------------------------------------------------- land cover

def _worldcover_tile(lat: float, lon: float) -> str:
    la, lo = int(np.floor(lat / 3) * 3), int(np.floor(lon / 3) * 3)
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'W' if lo < 0 else 'E'}{abs(lo):03d}"


def landcover_fractions(block: np.ndarray) -> Dict[str, float]:
    total = max(int((block > 0).sum()), 1)
    return {f"lc_{name}": float((block == code).sum() / total) for code, name in LANDCOVER_CLASSES.items()}


def landcover(points: pd.DataFrame, res: float) -> pd.DataFrame:
    """Land-cover fractions in a res x res box around each point."""
    rows = []
    opened: Dict[str, rasterio.DatasetReader] = {}
    try:
        for p in points.itertuples(index=False):
            tile = _worldcover_tile(p.lat, p.lon)
            src = opened.get(tile) or opened.setdefault(tile, rasterio.open(WORLDCOVER.format(tile=tile)))
            win = from_bounds(p.lon - res / 2, p.lat - res / 2, p.lon + res / 2, p.lat + res / 2, src.transform)
            d = landcover_fractions(src.read(1, window=win, out_shape=(12, 12), resampling=Resampling.nearest))
            d["point"] = p.point
            rows.append(d)
    finally:
        for src in opened.values():
            src.close()
    return pd.DataFrame(rows)[["point"] + LANDCOVER_COLUMNS]


def landcover_grid(bbox: Dict[str, float], res: float) -> pd.DataFrame:
    """Same as landcover() for a whole grid, reading each tile's overlap once (much faster)."""
    lat, lon = grid_centers(bbox, res)
    sub = 12
    rows = []
    tiles = {_worldcover_tile(a, b) for a in (bbox["min_lat"] + 1e-6, bbox["max_lat"] - 1e-6) for b in (bbox["min_lon"] + 1e-6, bbox["max_lon"] - 1e-6)}
    for tile in sorted(tiles):
        with rasterio.open(WORLDCOVER.format(tile=tile)) as src:
            b = src.bounds
            n, s = min(bbox["max_lat"], b.top), max(bbox["min_lat"], b.bottom)
            w, e = max(bbox["min_lon"], b.left), min(bbox["max_lon"], b.right)
            if n <= s or e <= w:
                continue
            h, wd = int(round((n - s) / res)), int(round((e - w) / res))
            arr = src.read(1, window=from_bounds(w, s, e, n, src.transform), out_shape=(h * sub, wd * sub), resampling=Resampling.nearest)
            arr = arr.reshape(h, sub, wd, sub).transpose(0, 2, 1, 3).reshape(h, wd, -1)
            r0, c0 = int(round((bbox["max_lat"] - n) / res)), int(round((w - bbox["min_lon"]) / res))
            for i in range(h):
                for j in range(wd):
                    d = landcover_fractions(arr[i, j])
                    d["point"] = f"cell_{(r0 + i) * len(lon) + (c0 + j)}"
                    rows.append(d)
    return pd.DataFrame(rows)[["point"] + LANDCOVER_COLUMNS]


# ---------------------------------------------------------------- elevation

def _dem_tile(lat: float, lon: float) -> str:
    la, lo = int(np.floor(lat)), int(np.floor(lon))
    return f"Copernicus_DSM_COG_10_{'N' if la >= 0 else 'S'}{abs(la):02d}_00_{'W' if lo < 0 else 'E'}{abs(lo):03d}_00_DEM"


def elevation(points: pd.DataFrame, res: float) -> pd.DataFrame:
    """Mean elevation (m) in a res x res box around each point."""
    rows = []
    opened: Dict[str, rasterio.DatasetReader] = {}
    try:
        for p in points.itertuples(index=False):
            tile = _dem_tile(p.lat, p.lon)
            src = opened.get(tile) or opened.setdefault(tile, rasterio.open(COPERNICUS_DEM.format(tile=tile)))
            win = from_bounds(p.lon - res / 2, p.lat - res / 2, p.lon + res / 2, p.lat + res / 2, src.transform)
            rows.append({"point": p.point, "elevation": float(np.nanmean(src.read(1, window=win, out_shape=(8, 8), resampling=Resampling.average)))})
    finally:
        for src in opened.values():
            src.close()
    return pd.DataFrame(rows)


def elevation_grid(bbox: Dict[str, float], res: float) -> pd.DataFrame:
    lat, lon = grid_centers(bbox, res)
    rows = []
    for la in range(int(np.floor(bbox["min_lat"])), int(np.ceil(bbox["max_lat"]))):
        for lo in range(int(np.floor(bbox["min_lon"])), int(np.ceil(bbox["max_lon"]))):
            n, s = min(bbox["max_lat"], la + 1), max(bbox["min_lat"], la)
            w, e = max(bbox["min_lon"], lo), min(bbox["max_lon"], lo + 1)
            if n <= s or e <= w:
                continue
            h, wd = int(round((n - s) / res)), int(round((e - w) / res))
            with rasterio.open(COPERNICUS_DEM.format(tile=_dem_tile(la + 0.5, lo + 0.5))) as src:
                arr = src.read(1, window=from_bounds(w, s, e, n, src.transform), out_shape=(h, wd), resampling=Resampling.average)
            r0, c0 = int(round((bbox["max_lat"] - n) / res)), int(round((w - bbox["min_lon"]) / res))
            for i in range(h):
                for j in range(wd):
                    rows.append({"point": f"cell_{(r0 + i) * len(lon) + (c0 + j)}", "elevation": float(arr[i, j])})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- roads and industry (OpenStreetMap)

def overpass_query(bboxes: Sequence[Dict[str, float]], kind: str) -> str:
    """kind 'roads' (motorway/trunk/primary) or 'industrial' (landuse polygons) in every bbox."""
    parts = []
    for b in bboxes:
        box = f"({b['min_lat']:.4f},{b['min_lon']:.4f},{b['max_lat']:.4f},{b['max_lon']:.4f})"
        if kind == "roads":
            parts.append(f'way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary)$"]{box};')
        else:
            parts.append(f'way["landuse"="industrial"]{box};relation["landuse"="industrial"]{box};')
    return f"[out:json][timeout:600];({''.join(parts)});out geom;"


def fetch_osm(bboxes: Sequence[Dict[str, float]], kind: str, dest: Path, client: Optional[httpx.Client] = None) -> dict:
    """Download (or reuse) the Overpass answer for bboxes as JSON at dest."""
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    own = client is None
    client = client or httpx.Client(timeout=900, headers={"User-Agent": USER_AGENT})
    try:
        r = client.post(OVERPASS, data={"data": overpass_query(bboxes, kind)})
        r.raise_for_status()
    finally:
        if own:
            client.close()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return r.json()


def local_metres(lat0: float):
    """A flat x/y (metres) projection centred on lat0: fine for distances of a few km."""
    k = 111_320.0
    cos0 = np.cos(np.radians(lat0))

    def to_m(lon, lat):
        return np.asarray(lon) * k * cos0, np.asarray(lat) * k

    return to_m


def road_features(points: pd.DataFrame, roads: dict, industrial: dict) -> pd.DataFrame:
    """Distances (m, capped) and road length (m) for each point."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.strtree import STRtree

    to_m = local_metres(float(points.lat.mean()))

    def project(coords):
        return list(zip(*to_m([c["lon"] for c in coords], [c["lat"] for c in coords])))

    big, prim = [], []
    for w in roads.get("elements", []):
        if w.get("type") == "way" and len(w.get("geometry", [])) > 1:
            (big if w["tags"]["highway"].startswith(("motorway", "trunk")) else prim).append(LineString(project(w["geometry"])))
    ind = []
    for w in industrial.get("elements", []):
        rings = [w["geometry"]] if w.get("type") == "way" else [m["geometry"] for m in w.get("members", []) if m.get("role") == "outer"]
        for ring in rings:
            if len(ring) > 3:
                poly = Polygon(project(ring)).buffer(0)
                if poly.is_valid and poly.area > 2000:
                    ind.append(poly)
    logger.info("OSM: %d motorway/trunk ways, %d primary ways, %d industrial areas", len(big), len(prim), len(ind))
    major = big + prim
    trees = {name: (STRtree(g) if g else None, g) for name, g in (("big", big), ("prim", prim), ("ind", ind), ("major", major))}

    def nearest(name, pt):
        tree, geoms = trees[name]
        return min(geoms[tree.nearest(pt)].distance(pt), ROAD_DISTANCE_CAP_M) if tree is not None else ROAD_DISTANCE_CAP_M

    rows = []
    for p in points.itertuples(index=False):
        x, y = to_m(p.lon, p.lat)
        pt = Point(float(x), float(y))
        buf = pt.buffer(1000)
        tree, geoms = trees["major"]
        length = sum(geoms[i].intersection(buf).length for i in tree.query(buf)) if tree is not None else 0.0
        rows.append({"point": p.point, "d_motorway": nearest("big", pt), "d_primary": nearest("prim", pt),
                     "road_len_1km": length, "d_industrial": nearest("ind", pt)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- weather (Open-Meteo ERA5)

def _weather_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["daily"])
    df["date"] = pd.to_datetime(df.pop("time"))
    hourly = pd.DataFrame(payload["hourly"])
    hourly["date"] = pd.to_datetime(hourly["time"]).dt.normalize()
    blh = hourly.groupby("date")["boundary_layer_height"].agg(blh_mean="mean", blh_min="min")
    df = df.merge(blh, left_on="date", right_index=True, how="left")
    rad = np.radians(df.pop("wind_direction_10m_dominant"))
    df["wind_u"] = df.wind_speed_10m_mean * np.sin(rad)
    df["wind_v"] = df.wind_speed_10m_mean * np.cos(rad)
    return df


def weather(points: pd.DataFrame, start: str, end: str, tz: str, client: Optional[httpx.Client] = None, batch: int = 10) -> pd.DataFrame:
    """Daily weather for each point: columns point, date, WEATHER_COLUMNS."""
    own = client is None
    client = client or httpx.Client(timeout=120)
    frames = []
    pts = list(points.itertuples(index=False))
    try:
        for i in range(0, len(pts), batch):
            chunk = pts[i : i + batch]
            params = {
                "latitude": ",".join(f"{p.lat:.4f}" for p in chunk), "longitude": ",".join(f"{p.lon:.4f}" for p in chunk),
                "start_date": start, "end_date": end, "daily": ",".join(WEATHER_DAILY),
                "hourly": "boundary_layer_height", "timezone": tz,
            }
            for attempt in range(5):
                r = client.get(OPEN_METEO_ARCHIVE, params=params)
                if r.status_code == 200:
                    break
                logger.warning("Open-Meteo HTTP %d, retrying", r.status_code)
                time.sleep(10 * (attempt + 1))
            r.raise_for_status()
            payloads = r.json()
            for p, payload in zip(chunk, payloads if isinstance(payloads, list) else [payloads]):
                df = _weather_frame(payload)
                df.insert(0, "point", p.point)
                frames.append(df)
            time.sleep(1.0)
    finally:
        if own:
            client.close()
    return pd.concat(frames, ignore_index=True)[["point", "date"] + WEATHER_COLUMNS]


def weather_lattice(bbox: Dict[str, float], step: float = WEATHER_LATTICE_DEG) -> pd.DataFrame:
    """Points at which to fetch weather for a grid; interpolated to cells by weather_to_grid."""
    rows = []
    for la in np.arange(bbox["min_lat"], bbox["max_lat"] + 1e-9, step):
        for lo in np.arange(bbox["min_lon"], bbox["max_lon"] + 1e-9, step):
            rows.append({"point": f"lat{la:.2f}_lon{lo:.2f}", "lat": round(float(la), 4), "lon": round(float(lo), 4)})
    return pd.DataFrame(rows)


def weather_to_grid(lattice_weather: pd.DataFrame, lattice: pd.DataFrame, day: pd.Timestamp, lat: np.ndarray, lon: np.ndarray) -> pd.DataFrame:
    """Linear interpolation of one day's lattice weather onto the grid cells (row-major)."""
    wd = lattice_weather[lattice_weather.date == day].merge(lattice, on="point")
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    out = {}
    for c in WEATHER_COLUMNS:
        v = griddata((wd.lat, wd.lon), wd[c], (la, lo), method="linear")
        n = griddata((wd.lat, wd.lon), wd[c], (la, lo), method="nearest")
        out[c] = np.where(np.isnan(v), n, v).ravel()
    return pd.DataFrame(out)


# ---------------------------------------------------------------- GEOS-CF daily grid

def sample_daily_tif(path: Path, lats: np.ndarray, lons: np.ndarray) -> Tuple[np.ndarray, List[str]]:
    """Bilinear sample of every band at the points -> (days, n) and the band dates."""
    with rasterio.open(path) as src:
        cube = src.read()
        tr = src.transform
        dates = list(src.descriptions)
    cols = (np.asarray(lons) - tr.c) / tr.a - 0.5
    rows = (np.asarray(lats) - tr.f) / tr.e - 0.5
    return np.stack([map_coordinates(band, [rows, cols], order=1, mode="nearest") for band in cube]), dates


# ---------------------------------------------------------------- calendar

def calendar(df: pd.DataFrame) -> pd.DataFrame:
    doy = df.date.dt.day_of_year
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    df["weekday"] = df.date.dt.day_of_week
    return df
