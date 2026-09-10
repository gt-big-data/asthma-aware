from pathlib import Path
import json

BACKEND_DIR = Path(__file__).resolve().parent.parent

CONFIG_DIR = BACKEND_DIR / "regions"
DATA_DIR = BACKEND_DIR / "data"

RAW_DATA_DIR = DATA_DIR / "raw"
NORMALIZED_DATA_DIR = DATA_DIR / "normalized"


def load_region(region_name: str = "atlanta") -> dict:
    path = CONFIG_DIR / f"{region_name}_region.json"

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)