"""Historical satellite grid; not the planned 500 m forecast grid.

This extent is retained for compatibility with existing raster history.
It does not cover downtown Atlanta and must not define API coverage.
"""


FEATURE_ORDER = ["so2", "ndvi", "no2"]

ATLANTA_BOUNDS = {
    "min_lat": 33.39,
    "max_lat": 33.64,
    "min_lon": -84.550,
    "max_lon": -84.280,
}

DEFAULT_CITY = "atlanta"