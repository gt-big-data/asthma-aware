# For basic settings like app name, version, and data file paths

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

CURRENT_MAP_FILE = DATA_DIR / "current_map.json"
FORECAST_MAP_FILE = DATA_DIR / "forecast_map.json"
CELL_DETAILS_FILE = DATA_DIR / "cell_details.json"