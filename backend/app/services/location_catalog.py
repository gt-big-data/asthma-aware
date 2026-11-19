"""Bundled Census Georgia places: no geocoder key or third-party search request."""
import json
from functools import lru_cache
from pathlib import Path

from app.services.risk_map import BOUNDS


@lru_cache(maxsize=1)
def catalog():
    data = json.loads((Path(__file__).parents[1] / "data" / "georgia_cities.json").read_text(encoding="utf-8"))
    return {city["id"]: city for city in data["cities"]}


@lru_cache(maxsize=1)
def boundary():
    return json.loads((Path(__file__).parents[1] / "data" / "georgia_boundary.json").read_text(encoding="utf-8"))["rings"]


def location_coverage(latitude, longitude):
    inside = False
    for ring in boundary():
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > latitude) != (y2 > latitude) and longitude < (x2 - x1) * (latitude - y1) / (y2 - y1) + x1:
                inside = not inside
    west, south, east, north = BOUNDS
    return {"in_georgia": inside, "forecast_supported": inside and west <= longitude < east and south < latitude <= north}


def city_by_id(city_id):
    city = catalog().get(city_id)
    if city is None:
        raise ValueError("Choose a city from the Georgia place list")
    west, south, east, north = BOUNDS
    return {**city, "state": "GA", "forecast_supported": west <= city["longitude"] < east and south < city["latitude"] <= north}


def search_cities(query, limit=20):
    query = query.strip().casefold()
    matches = [city for city in catalog().values() if query in city["name"].casefold()]
    if not query:
        popular = ("Atlanta", "Athens-Clarke County", "Augusta-Richmond County", "Macon-Bibb County", "Savannah", "Columbus", "Marietta", "Alpharetta", "Decatur", "Roswell")
        matches.sort(key=lambda c: (popular.index(c["name"]) if c["name"] in popular else len(popular), c["name"]))
    else:
        matches.sort(key=lambda c: (not c["name"].casefold().startswith(query), c["name"]))
    return [city_by_id(city["id"]) for city in matches[:limit]]
