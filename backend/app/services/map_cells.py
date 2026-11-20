"""Point queries share the raster's exact 0.01-degree indexing and scoring."""
import numpy as np

from app.services.location_catalog import location_coverage
from app.services.personalization_model import personal_scores
from app.services.risk_map import BOUNDS


def cell_details(artifact, index, latitude, longitude, fields=None, layer="risk", model=None):
    west, south, east, north = BOUNDS
    if not west <= longitude < east or not south < latitude <= north:
        raise ValueError("Location is outside the forecast grid")
    row = min(419, int(np.floor((north - latitude) / .01)))
    column = min(407, int(np.floor((longitude - west) / .01)))
    center_lat = round(north - (row + .5) * .01, 6)
    center_lon = round(west + (column + .5) * .01, 6)
    source_row, source_column = artifact.rows[row], artifact.columns[column]
    if model is None:
        score = artifact.general_scores[index, source_row, source_column]
    else:
        E = artifact.E[index:index + 1, :, source_row:source_row + 1, source_column:source_column + 1]
        score = personal_scores(E, artifact.ref, model)["score"].item()
    value, unit = score, "score / 100"
    if layer not in ("risk", "personal"):
        value = fields.frame(layer, artifact.times[index])[row, column]
        unit = fields.layers[layer]["unit"]
    finite = lambda number: float(number) if np.isfinite(number) else None
    return dict(row=row, column=column, latitude=center_lat, longitude=center_lon,
        bounds=[round(west + column * .01, 6), round(north - (row + 1) * .01, 6),
            round(west + (column + 1) * .01, 6), round(north - row * .01, 6)],
        risk_score=round(float(score)) if np.isfinite(score) else None,
        risk_kind="personal" if model is not None else "general", layer=layer,
        layer_value=finite(value), layer_unit=unit,
        valid_at=artifact.times[index].to_pydatetime(), source_run=artifact.metadata["source_run"],
        grid_step_degrees=.01, **location_coverage(center_lat, center_lon))
