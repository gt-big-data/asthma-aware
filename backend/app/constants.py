# Defines constants used across the application, such as feature order, geographic bounds for Atlanta, and the default city for predictions.

FEATURE_ORDER = ["so2", "ndvi", "no2"]

ATLANTA_BOUNDS = {
    "min_lat": 33.39,
    "max_lat": 33.64,
    "min_lon": -84.550,
    "max_lon": -84.280,
}

DEFAULT_CITY = "atlanta"