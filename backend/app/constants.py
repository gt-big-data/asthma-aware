# Defines constants used across the application, such as feature order, geographic bounds for Atlanta, and the default city for predictions.

FEATURE_ORDER = ["so2", "no2", "ozone", "ndvi", "pollen"]

ATLANTA_BOUNDS = {
    "min_lat": 33.640,
    "max_lat": 33.890,
    "min_lon": -84.550,
    "max_lon": -84.280,
}

DEFAULT_CITY = "atlanta"