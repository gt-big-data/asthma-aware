from app.utils.geo import get_lat_lon_for_cell


def parse_model_output_to_cells(grid, bounds):
    """
    grid shape: (3, 56, 96)
    feature order: [so2, ndvi, no2]
    """
    channels, rows, cols = grid.shape

    if channels != 3:
        raise ValueError(f"Expected 3 channels, got {channels}")

    cells = []

    for row in range(rows):
        for col in range(cols):
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
                "so2": float(grid[0, row, col]),
                "ndvi": float(grid[1, row, col]),
                "no2": float(grid[2, row, col]),
            }
            cells.append(cell)

    return cells