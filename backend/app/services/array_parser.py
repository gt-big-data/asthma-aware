# Parses a 2D grid of feature values into a structured list of cell dictionaries, 
# each containing geographic coordinates and feature values for that cell.

from app.constants import FEATURE_ORDER
from app.utils.geo import get_lat_lon_for_cell
from app.utils.validators import validate_grid_shape


def parse_grid_to_cells(grid, bounds):
    validate_grid_shape(grid)

    rows = len(grid)
    cols = len(grid[0])
    cells = []

    for row in range(rows):
        for col in range(cols):
            values = grid[row][col]

            lat, lon = get_lat_lon_for_cell(
                row=row,
                col=col,
                rows=rows,
                cols=cols,
                bounds=bounds
            )

            cell = {
                "row": row,
                "col": col,
                "lat": lat,
                "lon": lon,
                FEATURE_ORDER[0]: values[0],
                FEATURE_ORDER[1]: values[1],
                FEATURE_ORDER[2]: values[2],
                FEATURE_ORDER[3]: values[3],
                FEATURE_ORDER[4]: values[4],
            }
            cells.append(cell)

    return cells