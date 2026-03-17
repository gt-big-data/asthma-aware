# Validation logic to make sure the ML array shape is correct

def validate_grid_shape(grid):
    if not isinstance(grid, list) or len(grid) == 0:
        raise ValueError("Grid must be a non-empty list")

    first_row = grid[0]
    if not isinstance(first_row, list) or len(first_row) == 0:
        raise ValueError("Grid rows must be non-empty lists")

    expected_cols = len(first_row)

    for row in grid:
        if not isinstance(row, list):
            raise ValueError("Each row must be a list")

        if len(row) != expected_cols:
            raise ValueError("All rows must have the same number of columns")

        for cell in row:
            if not isinstance(cell, list):
                raise ValueError("Each cell must be a list")

            if len(cell) != 5:
                raise ValueError("Each cell must contain exactly 5 feature values")

            for value in cell:
                if not isinstance(value, (int, float)):
                    raise ValueError("Each feature value must be numeric")