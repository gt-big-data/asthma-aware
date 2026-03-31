from pathlib import Path
import numpy as np

from app.constants import ATLANTA_BOUNDS, DEFAULT_CITY
from app.services.array_parser import parse_model_output_to_cells
from app.services.forecast_service import generate_forecasts

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LATEST_SEQUENCE_PATH = DATA_DIR / "latest_observed_sequence.npy"

_forecast_cache = None


def load_latest_observed_sequence() -> np.ndarray:
    if not LATEST_SEQUENCE_PATH.exists():
        raise FileNotFoundError(
            f"Latest observed sequence file not found: {LATEST_SEQUENCE_PATH}"
        )

    sequence = np.load(LATEST_SEQUENCE_PATH)

    if sequence.shape != (4, 3, 56, 96):
        raise ValueError(
            f"Expected observed sequence shape (4, 3, 56, 96), got {sequence.shape}"
        )

    return sequence.astype(np.float32)


def get_current_map_data(city: str = DEFAULT_CITY):
    sequence = load_latest_observed_sequence()

    # Use the most recent observed frame from the 4-frame sequence
    current_frame = sequence[-1]  # shape (3, 56, 96)
    cells = parse_model_output_to_cells(current_frame, ATLANTA_BOUNDS)

    return {
        "city": city,
        "timestamp": "latest_observed_frame",
        "cells": cells
    }


def refresh_forecast_cache():
    global _forecast_cache
    latest_sequence = load_latest_observed_sequence()
    _forecast_cache = generate_forecasts(latest_sequence)


def get_forecast_data(horizon: str, city: str = DEFAULT_CITY):
    global _forecast_cache

    if _forecast_cache is None:
        refresh_forecast_cache()

    if horizon not in _forecast_cache:
        raise ValueError(f"Unsupported horizon: {horizon}")

    forecast_frame = _forecast_cache[horizon]
    cells = parse_model_output_to_cells(forecast_frame, ATLANTA_BOUNDS)

    return {
        "city": city,
        "forecast_horizon": horizon,
        "timestamp": f"predicted_{horizon}",
        "cells": cells
    }