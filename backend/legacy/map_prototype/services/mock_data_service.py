# Provides functions to load mock data for current and forecasted air quality grids from JSON files

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_current_grid():
    with open(DATA_DIR / "current_grid.json", "r") as f:
        return json.load(f)


def load_forecast_24h():
    with open(DATA_DIR / "forecast_24h.json", "r") as f:
        return json.load(f)


def load_forecast_48h():
    with open(DATA_DIR / "forecast_48h.json", "r") as f:
        return json.load(f)


def load_forecast_72h():
    with open(DATA_DIR / "forecast_72h.json", "r") as f:
        return json.load(f)