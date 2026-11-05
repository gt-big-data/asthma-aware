"""Score ConvLSTM SO2, NDVI, and NO2 forecasts against later observed frames.

The series is scaled with the same column-wise min/max used by
``scripts/build_latest_sequence.py``. Horizons are the recursive 24h, 48h,
and 72h forecasts from ``generate_forecasts``. The training end date of
``base_convlstm.pt`` is not in this repository, so the report is a
full-period check with unknown train overlap.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

from app.constants import FEATURE_ORDER
from app.services.evaluation_service import score_forecast
from app.services.preprocessing_service import EXPECTED_FRAME_SHAPE

BACKEND_DIR = Path(__file__).resolve().parents[2]
RASTER_DIR = BACKEND_DIR / "raw_rasters"

SO2_PATH = RASTER_DIR / "SO2_stack_reprojected_ndvi_dims2.tif"
NDVI_PATH = RASTER_DIR / "ndvi_stack.tif"
NO2_PATH = RASTER_DIR / "NO2_reprojected_stack.tif"
# Same file as app.services.ml_model_service.MODEL_PATH. Kept here so a
# missing-input check does not import torch.
CHECKPOINT_PATH = Path(__file__).resolve().parent / "base_convlstm.pt"

BAND0_DATE = dt.date(2023, 1, 1)
EXPECTED_DAYS = 181
SEQUENCE_LENGTH = 4
MAX_HORIZON_DAYS = 3
NDVI_COMPOSITE_DAYS = 16
HORIZONS = (("24h", 1), ("48h", 2), ("72h", 3))

FULL_PERIOD_LABEL = "full-period, train overlap unknown"


class MissingForecastInputs(FileNotFoundError):
    def __init__(self, paths):
        self.paths = list(paths)
        formatted = "\n".join(f"  {path}" for path in self.paths)
        super().__init__(
            "Missing files required for forecast evaluation:\n" + formatted
        )


def required_paths():
    return (SO2_PATH, NDVI_PATH, NO2_PATH, CHECKPOINT_PATH)


def missing_required_files():
    return [path for path in required_paths() if not path.exists()]


def read_raster_stack(path: Path):
    import rasterio

    with rasterio.open(path) as src:
        values = src.read().astype(np.float32)
        nodata = src.nodata
    return values, nodata


def channel_range(values, nodata=None):
    """Min, max, and non-finite fraction, treating a nodata sentinel as missing."""
    array = np.asarray(values, dtype=np.float64)
    if nodata is not None and np.isfinite(nodata):
        array = array.copy()
        array[np.isclose(array, nodata)] = np.nan
    finite = np.isfinite(array)
    nonfinite_fraction = float(1.0 - finite.mean()) if array.size else 1.0
    if not np.any(finite):
        return {
            "min": None,
            "max": None,
            "nonfinite_fraction": nonfinite_fraction,
        }
    return {
        "min": float(array[finite].min()),
        "max": float(array[finite].max()),
        "nonfinite_fraction": nonfinite_fraction,
    }


def scale_like_training_stack(stack: np.ndarray) -> np.ndarray:
    """Column-wise min/max after replacing NaN with 0.

    This matches ``minmax_scale_stack`` in ``scripts/build_latest_sequence.py``.
    Each of the width columns gets its own min and max.
    """
    from sklearn.preprocessing import MinMaxScaler

    filled = np.nan_to_num(stack).astype(np.float32)
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(filled.reshape(-1, filled.shape[-1])).reshape(filled.shape)
    return scaled.astype(np.float32)


def build_scaled_series(so2, ndvi, no2) -> np.ndarray:
    """Return ``(time, 3, height, width)`` in channel order so2, ndvi, no2."""
    if so2.shape != no2.shape:
        raise ValueError(
            "SO2 and NO2 stacks must have the same shape. "
            f"Got {so2.shape} and {no2.shape}."
        )
    if so2.shape[1:] != ndvi.shape[1:]:
        raise ValueError(
            "NDVI spatial shape must match SO2. "
            f"Got NDVI {ndvi.shape} and SO2 {so2.shape}."
        )
    if tuple(so2.shape[1:]) != tuple(EXPECTED_FRAME_SHAPE[1:]):
        raise ValueError(
            "Raster grid must be "
            f"{EXPECTED_FRAME_SHAPE[1]}x{EXPECTED_FRAME_SHAPE[2]}. Got {so2.shape[1:]}."
        )

    so2_scaled = scale_like_training_stack(so2)
    ndvi_scaled = scale_like_training_stack(ndvi)
    no2_scaled = scale_like_training_stack(no2)
    ndvi_daily = np.repeat(ndvi_scaled, NDVI_COMPOSITE_DAYS, axis=0)[: so2_scaled.shape[0]]
    return np.stack([so2_scaled, ndvi_daily, no2_scaled], axis=1).astype(np.float32)


def origin_indices(n_days, sequence_length=SEQUENCE_LENGTH, max_horizon=MAX_HORIZON_DAYS):
    """Last-observed indexes whose input window and longest target both exist."""
    first = sequence_length - 1
    last_exclusive = n_days - max_horizon
    if last_exclusive <= first:
        raise ValueError(
            f"Need at least {sequence_length + max_horizon} days to score a "
            f"{max_horizon}-day forecast. Got {n_days}."
        )
    return list(range(first, last_exclusive))


def recursive_forecasts(sequence: np.ndarray):
    """24h, 48h, and 72h frames using the serving recursion."""
    from app.services.forecast_service import generate_forecasts

    return generate_forecasts(sequence)


def _scaled_channel_range(series, channel_index):
    channel = series[:, channel_index]
    finite = np.isfinite(channel)
    return {
        "scaled_min": float(channel[finite].min()) if np.any(finite) else None,
        "scaled_max": float(channel[finite].max()) if np.any(finite) else None,
        "scaled_nonfinite_fraction": float(1.0 - finite.mean()),
    }


def evaluate_series(series: np.ndarray, forecast_fn=None) -> dict:
    """Score every valid origin in an already scaled ``(time, 3, H, W)`` series.

    ``forecast_fn`` defaults to the shipped recursive ConvLSTM. Tests can
    pass a stand-in that returns the same ``24h`` / ``48h`` / ``72h`` dict.
    """
    if series.ndim != 4 or series.shape[1] != len(FEATURE_ORDER):
        raise ValueError(
            f"Expected series shape (time, {len(FEATURE_ORDER)}, height, width), "
            f"got {series.shape}."
        )
    if forecast_fn is None:
        forecast_fn = recursive_forecasts

    origins = origin_indices(series.shape[0])
    n_origins = len(origins)
    shift = n_origins // 2
    predictions = {label: [] for label, _ in HORIZONS}

    for index, origin in enumerate(origins):
        if index == 0 or (index + 1) % 25 == 0 or index + 1 == n_origins:
            print(f"scoring origin {index + 1}/{n_origins}", flush=True)
        window = series[origin - (SEQUENCE_LENGTH - 1) : origin + 1]
        forecast = forecast_fn(window)
        for label, _ in HORIZONS:
            predictions[label].append(forecast[label])

    for label, _ in HORIZONS:
        predictions[label] = np.stack(predictions[label], axis=0)

    horizon_scores = {}
    for label, horizon_days in HORIZONS:
        targets = np.stack([series[origin + horizon_days] for origin in origins], axis=0)
        persistence = np.stack([series[origin] for origin in origins], axis=0)
        wrong_day = np.stack(
            [
                series[origins[(index + shift) % n_origins] + horizon_days]
                for index in range(n_origins)
            ],
            axis=0,
        )
        horizon_scores[label] = {
            name: score_forecast(
                predictions[label][:, channel],
                targets[:, channel],
                persistence[:, channel],
                wrong_day[:, channel],
            )
            for channel, name in enumerate(FEATURE_ORDER)
        }

    return {
        "n_origins": n_origins,
        "wrong_day_shift_origins": shift,
        "horizons": horizon_scores,
    }


def evaluate_rasters():
    """Load the training-style stacks and score the shipped ConvLSTM."""
    missing = missing_required_files()
    if missing:
        raise MissingForecastInputs(missing)

    so2, so2_nodata = read_raster_stack(SO2_PATH)
    ndvi, ndvi_nodata = read_raster_stack(NDVI_PATH)
    no2, no2_nodata = read_raster_stack(NO2_PATH)
    series = build_scaled_series(so2, ndvi, no2)

    raw_ranges = {
        "so2": channel_range(so2, so2_nodata),
        "ndvi": channel_range(ndvi, ndvi_nodata),
        "no2": channel_range(no2, no2_nodata),
    }
    for channel, name in enumerate(FEATURE_ORDER):
        raw_ranges[name].update(_scaled_channel_range(series, channel))

    n_days = int(series.shape[0])
    end_date = BAND0_DATE + dt.timedelta(days=n_days - 1)
    warnings = []
    if n_days != EXPECTED_DAYS:
        warnings.append(
            f"Expected {EXPECTED_DAYS} daily frames for 2023-01-01 through "
            f"2023-06-30, found {n_days}."
        )

    scores = evaluate_series(series)
    first_origin = origin_indices(n_days)[0]
    last_origin = origin_indices(n_days)[-1]

    return {
        "label": FULL_PERIOD_LABEL,
        "band0_date": BAND0_DATE.isoformat(),
        "end_date": end_date.isoformat(),
        "n_days": n_days,
        "expected_days": EXPECTED_DAYS,
        "first_origin_date": (BAND0_DATE + dt.timedelta(days=first_origin)).isoformat(),
        "last_origin_date": (BAND0_DATE + dt.timedelta(days=last_origin)).isoformat(),
        "scaler": (
            "column-wise minmax after nan_to_num, matching "
            "scripts/build_latest_sequence.py"
        ),
        "notes": (
            "Every origin whose 4-day input and 72-hour target fall inside the "
            "series is scored. The checkpoint training end date is unknown, so "
            "this is a full-period check with unknown train overlap. It asks "
            "whether the forecasts track the observed maps. It is not a held-out "
            "generalization score."
        ),
        "warnings": warnings,
        "channels": raw_ranges,
        **scores,
    }
