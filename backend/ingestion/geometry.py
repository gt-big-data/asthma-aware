# Contains functions to convert row/column positions into latitude/longitude values

def get_lat_lon_for_cell(row: int, col: int, rows: int, cols: int, bounds: dict):
    min_lat = bounds["min_lat"]
    max_lat = bounds["max_lat"]
    min_lon = bounds["min_lon"]
    max_lon = bounds["max_lon"]

    lat_step = (max_lat - min_lat) / rows
    lon_step = (max_lon - min_lon) / cols

    lat = max_lat - (row * lat_step)
    lon = min_lon + (col * lon_step)

    return lat, lon