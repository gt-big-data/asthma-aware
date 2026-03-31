import numpy as np
from app.services.array_parser import parse_model_output_to_cells
from app.constants import ATLANTA_BOUNDS


def test_parse_model_output_to_cells():
    grid = np.zeros((3, 56, 96), dtype=np.float32)
    grid[0, 0, 0] = 1.1
    grid[1, 0, 0] = 2.2
    grid[2, 0, 0] = 3.3

    cells = parse_model_output_to_cells(grid, ATLANTA_BOUNDS)

    assert len(cells) == 56 * 96
    first = cells[0]

    assert first["row"] == 0
    assert first["col"] == 0
    assert "lat" in first
    assert "lon" in first
    assert first["so2"] == 1.1
    assert first["ndvi"] == 2.2
    assert first["no2"] == 3.3