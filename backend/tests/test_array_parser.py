# Tests whether the raw array is being parsed correctly

from app.services.array_parser import parse_grid_to_cells
from app.constants import ATLANTA_BOUNDS


def test_parse_grid_to_cells():
    grid = [
        [
            [42.1, 78.5, 3.2, 65.0, 0.71]
        ]
    ]

    cells = parse_grid_to_cells(grid, ATLANTA_BOUNDS)

    assert len(cells) == 1
    assert cells[0]["aqi"] == 42.1
    assert cells[0]["pollen"] == 78.5
    assert cells[0]["smoke"] == 3.2
    assert cells[0]["weather"] == 65.0
    assert cells[0]["risk_score"] == 0.71