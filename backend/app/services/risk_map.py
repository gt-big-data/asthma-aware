"""Hourly population-risk rasters from the same versioned artifact as the dashboard.

Artifacts may contain native shared scores or individual 0.01-degree scores.
Read only one frame, then encode a transparent PNG in memory.
No database, filesystem writes, image library, or public bucket is required.
"""
import base64
import hashlib
import struct
import zlib
from collections import OrderedDict
from threading import Lock
from datetime import timedelta
from functools import lru_cache

import numpy as np
from pydantic import AwareDatetime, BaseModel, Field

from app.services.geos_dashboard import ForecastUnavailable
from app.services.risk_artifacts import LATITUDES

# Edges of the actual model footprint, not an assertion of statewide coverage.
BOUNDS = [-85.6, 30.78, -81.52, 34.98]  # west, south, east, north
COLORS = ["#2547a8", "#20b9c7", "#69cf71", "#f4d84a", "#e53935"]


class MapLayer(BaseModel):
    id: str
    label: str
    unit: str
    minimum: float
    maximum: float
    colors: list[str]
    description: str
    processing: str
    experimental: bool = False


RISK_LAYER = MapLayer(id="risk", label="General risk", unit="score / 100", minimum=0, maximum=100,
    colors=COLORS, description="General population risk · not personalized",
    processing="Four-pollutant rolling exposure model")


class LayerUnavailable(ValueError):
    pass


class MapFrame(BaseModel):
    index: int
    valid_at: AwareDatetime
    available: bool


class MapForecast(BaseModel):
    run_id: str
    source_run: str
    issued_at: AwareDatetime
    stale: bool
    provisional: bool
    grid_step_degrees: float = 0.01
    bounds: list[float] = BOUNDS
    colors: list[str] = COLORS
    score_min: int = 0
    score_max: int = 100
    frames: list[MapFrame]
    personalization: dict | None = None
    layer: MapLayer = RISK_LAYER
    layers: list[MapLayer] = Field(default_factory=lambda: [RISK_LAYER])


def map_forecast(artifact, now, fields=None, layer="risk"):
    from pandas import Timestamp

    issued = Timestamp(artifact.metadata["issued_at"])
    issued = issued.tz_localize("UTC") if issued.tzinfo is None else issued.tz_convert("UTC")
    selected = np.flatnonzero(
        (artifact.times >= issued) & (artifact.times + timedelta(minutes=30) > now)
        & (artifact.times <= now + timedelta(hours=96))
    )
    if artifact.metadata["product"] != "forecast" or issued > now or not len(selected):
        raise ForecastUnavailable("No current or future risk frames")
    # Do not materialize the full fine-grid time cube just to count coverage.
    source_rows, source_columns = np.unique(artifact.rows), np.unique(artifact.columns)
    layers = [RISK_LAYER, *[MapLayer(**spec) for spec in fields.layers.values()]] if fields else [RISK_LAYER]
    selected_layer = next((item for item in layers if item.id == layer), None)
    if selected_layer is None:
        raise LayerUnavailable("This map layer has not been published")
    if layer == "risk":
        available = {int(i): bool(np.isfinite(artifact.general_scores[i][np.ix_(source_rows, source_columns)]).any()) for i in selected}
        run_id = artifact.digest
    else:
        indices = fields.times.get_indexer(artifact.times[selected])
        available = {int(i): bool(index >= 0 and fields.available[layer][index]) for i, index in zip(selected, indices)}
        run_id = hashlib.sha256(f"physical-map-v2:{artifact.digest}:{fields.digest}:{layer}".encode()).hexdigest()
    return MapForecast(
        run_id=run_id, source_run=artifact.metadata["source_run"],
        issued_at=issued.to_pydatetime(), stale=now - issued > timedelta(hours=24),
        provisional=artifact.metadata["provisional"],
        layer=selected_layer, layers=layers, colors=selected_layer.colors,
        score_min=selected_layer.minimum, score_max=selected_layer.maximum,
        frames=[MapFrame(index=int(i), valid_at=artifact.times[i].to_pydatetime(),
                         available=available[int(i)])
                for i in selected],
    )


def frame_rgba(artifact, index):
    scores = artifact.general_scores[index][np.ix_(artifact.rows, artifact.columns)]
    return colorize_frame(scores, COLORS, 0, 100)


def colorize_frame(scores, colors, minimum, maximum):
    # Image sources use Mercator texture coordinates. Reproject rows so the
    # 0.01-degree grid stays aligned with locations on the MapLibre base map.
    north = np.log(np.tan(np.pi / 4 + np.deg2rad(BOUNDS[3]) / 2))
    south = np.log(np.tan(np.pi / 4 + np.deg2rad(BOUNDS[1]) / 2))
    # Oversample the warp so no source latitude row disappears during projection.
    height = len(LATITUDES) * 4
    y = north - (np.arange(height) + 0.5) / height * (north - south)
    latitudes = np.rad2deg(2 * np.arctan(np.exp(y)) - np.pi / 2)
    rows = np.clip(np.floor((BOUNDS[3] - latitudes) / 0.01).astype(int), 0, len(LATITUDES) - 1)
    scores = scores[rows]
    valid = np.isfinite(scores)
    values = np.nan_to_num(scores, nan=minimum).clip(minimum, maximum)
    palette = np.array([[int(color[i:i + 2], 16) for i in (1, 3, 5)] for color in colors])
    ticks = np.linspace(minimum, maximum, len(colors))
    rgb = np.stack([np.interp(values, ticks, palette[:, c]) for c in range(3)], axis=-1)
    return np.concatenate([np.rint(rgb).astype(np.uint8), (valid.astype(np.uint8) * 255)[..., None]], axis=-1)


def encode_png(rgba):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    height, width, _ = rgba.shape
    raw = b"".join(b"\x00" + row.tobytes() for row in rgba)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def map_image(artifact, index, fields=None, layer="risk"):
    """Bounded per-process cache; immutable artifact identity prevents mixed runs."""
    key = (artifact.digest, fields.digest if fields and layer != "risk" else None, layer, index)
    with _image_lock:
        if key in _images:
            _images.move_to_end(key)
            return _images[key]
    if layer == "risk":
        rgba = frame_rgba(artifact, index)
    else:
        spec = fields.layers[layer]
        rgba = colorize_frame(fields.frame(layer, artifact.times[index]), spec["colors"], spec["minimum"], spec["maximum"])
    result = "data:image/png;base64," + base64.b64encode(encode_png(rgba)).decode("ascii")
    with _image_lock:
        _images[key] = result
        while len(_images) > 24:
            _images.popitem(last=False)
    return result


_images = OrderedDict()
_image_lock = Lock()
