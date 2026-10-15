"""Loaders for the mock JSON fixtures that stand in for unbuilt components.

Results are cached and shared, so callers must not mutate them.
"""

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(filename: str) -> dict:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_mock_forecast() -> dict:
    return _load("mock_forecast.json")


@lru_cache(maxsize=None)
def load_user_personalization() -> dict:
    return _load("mock_user_personalization.json")
