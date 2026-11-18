"""Population map consistency, spatial alignment, run transitions and cloud reads."""
import base64
import json
import struct
import zlib
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import utc_now
from app.main import create_app
from app.services import gcs_forecast as gcs
from app.services.geos_dashboard import PublishedForecast
from app.services.personalization_model import FEATURE_VERSION, MODEL_VERSION, general_risk
from app.services.risk_artifacts import PREPROCESSING, load_artifact
from app.services.risk_dashboard import risk_dashboard
from app.services.risk_map import BOUNDS, frame_rgba


@pytest.fixture
def published(tmp_path):
    issued = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)
    times = np.datetime64("2026-11-10T09:30") + np.arange(120) * np.timedelta64(1, "h")
    E = np.ones((120, 4, 2, 3), dtype=np.float32)
    E *= np.arange(1, 121)[:, None, None, None]
    E[:, :, 1, 2] = np.nan  # Missing southeast region must stay transparent.
    E[:, :, 0, 1] *= 0.3
    path = tmp_path / "risk.npz"
    fields = dict(times=times.astype("datetime64[ns]").astype(np.int64), E=E,
                  source_rows=np.repeat([0, 1], 210), source_columns=np.repeat([0, 1, 2], 136),
                  mean=np.ones(4), std=np.ones(4), clean=np.zeros(4), eta_sorted=np.linspace(0, 5, 1000),
                  metadata=json.dumps(dict(model_version=MODEL_VERSION, feature_version=FEATURE_VERSION,
                                           preprocessing=PREPROCESSING, reference_id="test", provisional=True,
                                           issued_at=issued.isoformat(), source_run="test", product="forecast")))
    np.savez_compressed(path, **fields)
    artifact = load_artifact(path)
    run = PublishedForecast(source_run="test", issued_at=issued,
                            times=[issued + timedelta(minutes=30, hours=i) for i in range(120)],
                            latitudes=[33.75], longitudes=[-84.5], pm25=[[10.] for _ in times],
                            risk_artifact="risk.npz")
    (tmp_path / "current.json").write_text(run.model_dump_json())
    state = {"now": issued + timedelta(hours=32)}
    app = create_app(Settings(dashboard_forecast_path=str(tmp_path / "current.json")))
    app.dependency_overrides[utc_now] = lambda: state["now"]
    with TestClient(app) as client:
        yield client, artifact, run, state, path, fields


def decode_image(image):
    data = base64.b64decode(image.split(",")[1])
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset, compressed, dimensions = 8, b"", None
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + length]
        assert zlib.crc32(kind + chunk) & 0xffffffff == struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        if kind == b"IHDR":
            dimensions = struct.unpack(">II", chunk[:8])
        if kind == b"IDAT":
            compressed += chunk
        offset += length + 12
    width, height = dimensions
    rows = np.frombuffer(zlib.decompress(compressed), dtype=np.uint8).reshape(height, width * 4 + 1)
    assert not rows[:, 0].any()
    return rows[:, 1:].reshape(height, width, 4)


def test_map_remaining_hours_and_dashboard_general_scores_agree(published):
    client, artifact, _, state, _, _ = published
    response = client.get("/v1/map/forecast")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["stale"] and metadata["provisional"]
    assert len(metadata["frames"]) == 88
    assert metadata["frames"][0]["index"] == 32
    assert metadata["bounds"] == BOUNDS
    assert metadata["grid_step_degrees"] == 0.01
    dashboard = risk_dashboard(artifact, 33.75, -84.39, state["now"])
    E = artifact.point(33.75, -84.39)
    np.testing.assert_allclose(artifact.general_scores[:, artifact.rows[123], artifact.columns[121]],
                               general_risk(E, artifact.ref).score[:, 0, 0])
    # Dashboard daily maxima must be computed from these same hourly scores.
    from zoneinfo import ZoneInfo
    for day in dashboard.days:
        indices = [f["index"] for f in metadata["frames"] if artifact.times[f["index"]].tz_convert(ZoneInfo("America/New_York")).date() == day.date]
        expected = round(float(artifact.general_scores[indices, 0, 0].max())) if indices else None
        assert day.general_score == expected
    state["now"] += timedelta(hours=88)
    assert client.get("/v1/map/forecast").status_code == 503


def test_raster_keeps_missing_cells_transparent_and_fixed_scale(published):
    client, artifact, _, _, _, _ = published
    metadata = client.get("/v1/map/forecast").json()
    response = client.get("/v1/map/frame", params={"run_id": metadata["run_id"], "index": 40})
    assert response.status_code == 200
    image = decode_image(response.json()["image"])
    assert image.shape == (1680, 408, 4)
    assert image[0, 0, 3] == 255 and image[-1, -1, 3] == 0
    # The same score has the same color in different hours, without per-frame stretching.
    artifact.general_scores[41, 0, 0] = artifact.general_scores[40, 0, 0]
    np.testing.assert_array_equal(frame_rgba(artifact, 40)[0, 0], frame_rgba(artifact, 41)[0, 0])
    assert not np.array_equal(image[0, 0, :3], image[0, 180, :3])


def test_run_pinning_invalid_hours_and_public_cors(published):
    client, artifact, _, _, _, _ = published
    assert client.get("/v1/map/frame", params={"run_id": "0" * 64, "index": 40}).status_code == 409
    for index in (0, 120, 999999):
        assert client.get("/v1/map/frame", params={"run_id": artifact.digest, "index": index}).status_code == 404
    assert client.get("/v1/map/frame", params={"run_id": "../secret", "index": 40}).status_code == 422
    response = client.get("/v1/map/forecast", headers={"Origin": "http://localhost:8081"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:8081"
    assert response.headers["cache-control"] == "no-store"


def test_mercator_raster_alignment_and_completely_missing_hour(published):
    client, artifact, _, _, _, _ = published
    artifact.general_scores[45] = [[0, 25, 50], [75, 100, np.nan]]
    image = frame_rgba(artifact, 45)
    from app.services.risk_map import COLORS
    west, south, east, north = BOUNDS
    mercator = lambda lat: np.log(np.tan(np.pi / 4 + np.deg2rad(lat) / 2))
    def pixel(lat, lon):
        row = int((mercator(north) - mercator(lat)) / (mercator(north) - mercator(south)) * image.shape[0])
        col = int((lon - west) / (east - west) * image.shape[1])
        return image[row, col]
    for lat, lon, color in [(34., -85., 0), (34., -84., 1), (34., -82., 2),
                            (31., -85., 3), (31., -84., 4)]:
        expected = [int(COLORS[color][i:i + 2], 16) for i in (1, 3, 5)] + [255]
        np.testing.assert_array_equal(pixel(lat, lon), expected)
    assert pixel(31., -82.)[3] == 0
    artifact.general_scores[46] = np.nan
    metadata = client.get("/v1/map/forecast").json()
    assert not next(frame for frame in metadata["frames"] if frame["index"] == 46)["available"]
    result = client.get("/v1/map/frame", params={"run_id": artifact.digest, "index": 46}).json()
    assert not result["available"]
    assert not decode_image(result["image"])[..., 3].any()


def test_missing_artifact_fails_without_demo_fallback(tmp_path):
    with TestClient(create_app(Settings(dashboard_forecast_path=str(tmp_path / "missing.json")))) as client:
        assert client.get("/v1/map/forecast").status_code == 503


def test_persisted_scores_and_legacy_artifacts_match(published):
    _, artifact, _, _, path, fields = published
    fields["general_scores"] = artifact.general_scores
    np.savez_compressed(path, **fields)
    np.testing.assert_allclose(load_artifact(path).general_scores, artifact.general_scores, equal_nan=True)
    fields["general_scores"] = np.ones((1, 2, 3))
    np.savez_compressed(path, **fields)
    with pytest.raises(ValueError, match="score dimensions"):
        load_artifact(path)


def test_gcs_map_reads_private_publication_and_reuses_download(published, monkeypatch, tmp_path):
    _, artifact, run, state, path, _ = published
    gcs.read_artifact.cache_clear()
    gcs.cached_risk_file.cache_clear()
    bucket = Mock()
    pointer = gcs.Publication(publication_id=uuid4(), issued_at=run.issued_at, has_risk=True)
    def blob(key):
        result = Mock()
        if key == gcs.CURRENT:
            result.download_as_bytes.return_value = pointer.model_dump_json().encode()
        elif key == pointer.artifact:
            result.download_as_bytes.return_value = run.model_dump_json().encode()
        else:
            result.download_to_filename.side_effect = lambda target, **kwargs: target.write_bytes(path.read_bytes())
        return result
    bucket.blob.side_effect = blob
    monkeypatch.setattr(gcs, "get_bucket", lambda name: bucket)
    monkeypatch.setattr(gcs, "gettempdir", lambda: str(tmp_path))
    app = create_app(Settings(forecast_gcs_bucket="test-map"))
    app.dependency_overrides[utc_now] = lambda: state["now"]
    with TestClient(app) as client:
        metadata = client.get("/v1/map/forecast").json()
        assert metadata["run_id"] == artifact.digest
        assert client.get("/v1/map/frame", params={"run_id": artifact.digest, "index": 40}).status_code == 200
    assert sum(call.args[0].endswith("risk.npz") for call in bucket.blob.call_args_list) == 1
