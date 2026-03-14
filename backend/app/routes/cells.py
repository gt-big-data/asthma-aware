from fastapi import APIRouter, HTTPException

from app.config import CELL_DETAILS_FILE
from app.services.data_loader import load_json_file

router = APIRouter()


@router.get("/{cell_id}")
def get_cell_details(cell_id: str):
    data = load_json_file(CELL_DETAILS_FILE)
    if data is None:
        raise HTTPException(status_code=404, detail="Cell details data not found")

    # assuming data is a list of cell detail objects
    for cell in data:
        if cell.get("cell_id") == cell_id:
            return cell

    raise HTTPException(status_code=404, detail=f"Cell {cell_id} not found")