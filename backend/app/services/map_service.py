# Provides functions to retrieve and process current and forecasted air quality data for a specified city

from app.constants import ATLANTA_BOUNDS, DEFAULT_CITY
from app.services.mock_data_service import (
    load_current_grid,
    load_forecast_24h,
    load_forecast_48h,
    load_forecast_72h,
)
from app.services.array_parser import parse_grid_to_cells


def get_current_map_data(city: str = DEFAULT_CITY):
    raw_data = load_current_grid()
    grid = raw_data["grid"]
    timestamp = raw_data["timestamp"]

    cells = parse_grid_to_cells(grid, ATLANTA_BOUNDS)

    return {
        "city": city,
        "timestamp": timestamp,
        "cells": cells
    }


def get_forecast_24h_data(city: str = DEFAULT_CITY):
    raw_data = load_forecast_24h()
    grid = raw_data["grid"]
    timestamp = raw_data["timestamp"]

    cells = parse_grid_to_cells(grid, ATLANTA_BOUNDS)

    return {
        "city": city,
        "forecast_horizon": "24h",
        "timestamp": timestamp,
        "cells": cells
    }


def get_forecast_48h_data(city: str = DEFAULT_CITY):
    raw_data = load_forecast_48h()
    grid = raw_data["grid"]
    timestamp = raw_data["timestamp"]

    cells = parse_grid_to_cells(grid, ATLANTA_BOUNDS)

    return {
        "city": city,
        "forecast_horizon": "48h",
        "timestamp": timestamp,
        "cells": cells
    }


def get_forecast_72h_data(city: str = DEFAULT_CITY):
    raw_data = load_forecast_72h()
    grid = raw_data["grid"]
    timestamp = raw_data["timestamp"]

    cells = parse_grid_to_cells(grid, ATLANTA_BOUNDS)

    return {
        "city": city,
        "forecast_horizon": "72h",
        "timestamp": timestamp,
        "cells": cells
    }