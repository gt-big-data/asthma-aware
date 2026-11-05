"""Download the July-August 2023 test stacks and score the ConvLSTM.

The notebook MainConvLSTMModel.ipynb requests 2023-07-01 through 2023-09-01
(end exclusive) over the Atlanta box. This script pulls SO2, NO2, and
MOD13A1 NDVI for that window onto the 56x96 model grid, writes GeoTIFFs
under raw_rasters/test data, and scores them with the column min/max fit
on the January-June training stacks.

Earth Engine calls are billed to --ee-project. The project does not change
the satellite values.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.constants import ATLANTA_BOUNDS, FEATURE_ORDER  # noqa: E402
from app.ml.forecast_eval import (  # noqa: E402
    NDVI_COMPOSITE_DAYS,
    NDVI_PATH,
    NO2_PATH,
    SO2_PATH,
    evaluate_series,
    read_raster_stack,
    scale_like_training_stack,
)
from ingestion.grid_resample import target_transform  # noqa: E402
from sklearn.preprocessing import MinMaxScaler  # noqa: E402

START_DATE = dt.date(2023, 7, 1)
END_DATE = dt.date(2023, 9, 1)  # exclusive, matching the notebook
ROWS = 56
COLS = 96
NODATA = -9999.0
TEST_DIR = BACKEND_DIR / "raw_rasters" / "test data"


def daterange(start: dt.date, end: dt.date):
    day = start
    while day < end:
        yield day
        day += dt.timedelta(days=1)


def column_scaler(stack: np.ndarray) -> MinMaxScaler:
    filled = np.nan_to_num(np.asarray(stack, dtype=np.float64))
    scaler = MinMaxScaler()
    scaler.fit(filled.reshape(-1, filled.shape[-1]))
    return scaler


def apply_scaler(stack: np.ndarray, scaler: MinMaxScaler) -> np.ndarray:
    filled = np.nan_to_num(np.asarray(stack, dtype=np.float64))
    scaled = scaler.transform(filled.reshape(-1, filled.shape[-1]))
    return scaled.reshape(filled.shape).astype(np.float32)


def write_stack(path: Path, stack: np.ndarray, transform) -> None:
    count, height, width = stack.shape
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": count,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
        "nodata": NODATA,
    }
    stored = np.where(np.isfinite(stack), stack, NODATA).astype(np.float32)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(stored)
        for index, day_offset in enumerate(range(count), start=1):
            dst.set_band_description(index, f"band {index}")


def fetch_grid(image, band: str, region, crs_transform) -> np.ndarray:
    import ee

    projected = image.select([band]).reproject(crs="EPSG:4326", crsTransform=crs_transform)
    sampled = projected.sampleRectangle(region=region, defaultValue=NODATA)
    array = np.array(sampled.get(band).getInfo(), dtype=np.float32)
    if array.shape != (ROWS, COLS):
        raise ValueError(f"{band} grid is {array.shape}, expected {(ROWS, COLS)}")
    array[np.isclose(array, NODATA)] = np.nan
    return array


def daily_mean(collection_id: str, band: str, day: dt.date, region):
    import ee

    start = day.isoformat()
    end = (day + dt.timedelta(days=1)).isoformat()
    collection = (
        ee.ImageCollection(collection_id)
        .filterDate(start, end)
        .filterBounds(region)
        .select(band)
    )
    if collection.size().getInfo() == 0:
        return None
    return collection.mean()


def with_retries(action, label: str, attempts: int = 4):
    import ee

    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except (ee.EEException, ValueError) as exc:
            if attempt == attempts:
                raise
            print(f"retry {attempt} for {label}: {exc}", flush=True)
            time.sleep(delay)
            delay *= 2
    return None


def download(project: str) -> dict:
    import ee

    ee.Initialize(project=project)
    bounds = ATLANTA_BOUNDS
    region = ee.Geometry.Rectangle(
        [bounds["min_lon"], bounds["min_lat"], bounds["max_lon"], bounds["max_lat"]]
    )
    transform = target_transform(bounds, ROWS, COLS)
    crs_transform = [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f]
    days = list(daterange(START_DATE, END_DATE))

    so2_frames = []
    no2_frames = []
    missing = {"so2": [], "no2": []}
    for day in days:
        so2_image = with_retries(
            lambda day=day: daily_mean(
                "COPERNICUS/S5P/OFFL/L3_SO2", "SO2_column_number_density", day, region
            ),
            f"SO2 {day}",
        )
        no2_image = with_retries(
            lambda day=day: daily_mean(
                "COPERNICUS/S5P/OFFL/L3_NO2",
                "tropospheric_NO2_column_number_density",
                day,
                region,
            ),
            f"NO2 {day}",
        )
        if so2_image is None:
            so2_frames.append(np.full((ROWS, COLS), np.nan, dtype=np.float32))
            missing["so2"].append(day.isoformat())
        else:
            so2_frames.append(
                with_retries(
                    lambda image=so2_image: fetch_grid(
                        image, "SO2_column_number_density", region, crs_transform
                    ),
                    f"SO2 grid {day}",
                )
            )
        if no2_image is None:
            no2_frames.append(np.full((ROWS, COLS), np.nan, dtype=np.float32))
            missing["no2"].append(day.isoformat())
        else:
            no2_frames.append(
                with_retries(
                    lambda image=no2_image: fetch_grid(
                        image,
                        "tropospheric_NO2_column_number_density",
                        region,
                        crs_transform,
                    ),
                    f"NO2 grid {day}",
                )
            )
        print(f"downloaded {day.isoformat()}", flush=True)

    ndvi_collection = (
        ee.ImageCollection("MODIS/061/MOD13A1")
        .filterDate(START_DATE.isoformat(), END_DATE.isoformat())
        .filterBounds(region)
        .select("NDVI")
        .sort("system:time_start")
    )
    ndvi_count = ndvi_collection.size().getInfo()
    ndvi_frames = []
    ndvi_dates = []
    for index in range(ndvi_count):
        image = ee.Image(ndvi_collection.toList(ndvi_count).get(index))
        millis = image.get("system:time_start").getInfo()
        source_date = dt.datetime.fromtimestamp(millis / 1000, dt.timezone.utc).date()
        frame = with_retries(
            lambda image=image: fetch_grid(image, "NDVI", region, crs_transform),
            f"NDVI {source_date}",
        )
        ndvi_frames.append(frame)
        ndvi_dates.append(source_date.isoformat())
        print(f"downloaded NDVI composite {source_date.isoformat()}", flush=True)

    TEST_DIR.mkdir(parents=True, exist_ok=True)
    so2 = np.stack(so2_frames)
    no2 = np.stack(no2_frames)
    ndvi = np.stack(ndvi_frames) if ndvi_frames else np.zeros((0, ROWS, COLS), np.float32)
    write_stack(TEST_DIR / "SO2_stack_reprojected_ndvi_dims2.tif", so2, transform)
    write_stack(TEST_DIR / "NO2_reprojected_stack.tif", no2, transform)
    write_stack(TEST_DIR / "ndvi_stack.tif", ndvi, transform)
    manifest = {
        "ee_project": project,
        "start_date": START_DATE.isoformat(),
        "end_date_exclusive": END_DATE.isoformat(),
        "n_days": len(days),
        "daily_dates": [day.isoformat() for day in days],
        "ndvi_composite_dates": ndvi_dates,
        "missing_so2": missing["so2"],
        "missing_no2": missing["no2"],
        "grid": {"rows": ROWS, "cols": COLS},
    }
    (TEST_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"so2": so2, "no2": no2, "ndvi": ndvi, "manifest": manifest}


def score_held_out(test: dict) -> dict:
    train_so2, _ = read_raster_stack(SO2_PATH)
    train_ndvi, _ = read_raster_stack(NDVI_PATH)
    train_no2, _ = read_raster_stack(NO2_PATH)
    so2_scaler = column_scaler(train_so2)
    ndvi_scaler = column_scaler(train_ndvi)
    no2_scaler = column_scaler(train_no2)

    refit = scale_like_training_stack(train_so2)
    check = apply_scaler(train_so2, so2_scaler)
    if not np.allclose(refit, check, atol=1e-5, equal_nan=True):
        raise RuntimeError("Training scaler does not match scale_like_training_stack.")

    so2 = apply_scaler(test["so2"], so2_scaler)
    no2 = apply_scaler(test["no2"], no2_scaler)
    ndvi = apply_scaler(test["ndvi"], ndvi_scaler)
    ndvi_daily = np.repeat(ndvi, NDVI_COMPOSITE_DAYS, axis=0)[: so2.shape[0]]
    series = np.stack([so2, ndvi_daily, no2], axis=1).astype(np.float32)
    scores = evaluate_series(series)
    return {
        "label": "held-out 2023-07-01 through 2023-08-31",
        "scaler": "column min/max fit on the January-June 2023 training stacks only",
        "band0_date": START_DATE.isoformat(),
        "end_date": (END_DATE - dt.timedelta(days=1)).isoformat(),
        "n_days": int(series.shape[0]),
        "manifest": test["manifest"],
        **scores,
    }


def print_report(report: dict) -> None:
    print(report["label"])
    print(f"days={report['n_days']} origins={report['n_origins']}")
    manifest = report["manifest"]
    print(
        f"missing so2={len(manifest['missing_so2'])} "
        f"missing no2={len(manifest['missing_no2'])} "
        f"ndvi composites={len(manifest['ndvi_composite_dates'])}"
    )
    for horizon, scores in report["horizons"].items():
        for name in FEATURE_ORDER:
            score = scores[name]
            correlation = score["correlation"]
            corr_text = "undefined" if correlation is None else f"{correlation:.3f}"
            print(
                f"{horizon} {name}  "
                f"mae={score['mae']:.4f}  "
                f"persistence={score['mae_persistence']:.4f}  "
                f"wrong_day={score['mae_wrong_day']:.4f}  "
                f"corr={corr_text}  "
                f"pass={score['passes_not_random']}"
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ee-project", default="asthma-ml-project")
    args = parser.parse_args(argv)
    test = download(args.ee_project)
    report = score_held_out(test)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = BACKEND_DIR / "app" / "ml" / "eval_runs" / f"{stamp}_heldout"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "metrics.json"

    def ready(value):
        if isinstance(value, dict):
            return {str(key): ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ready(item) for item in value]
        if isinstance(value, (np.floating, float)):
            number = float(value)
            return number if np.isfinite(number) else None
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.bool_,)):
            return bool(value)
        return value

    output_path.write_text(json.dumps(ready(report), indent=2), encoding="utf-8")
    print_report(report)
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
