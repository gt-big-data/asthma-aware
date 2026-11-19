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


def test_physical_layer_routes_pin_layer_identity_and_reject_missing_layers(published, monkeypatch):
    from app.api import risk_map as routes
    from app.services.map_fields import SPECS, POLLUTION_COLORS
    from app.services.risk_map import map_forecast
    from types import SimpleNamespace
    client, artifact, _, state, _, _ = published
    label, unit, low, high = SPECS["no2"]
    fields = SimpleNamespace(digest="b" * 64, times=artifact.times,
        available={"no2": np.ones(len(artifact.times), bool)},
        layers={"no2": dict(id="no2", label=label, unit=unit, minimum=low, maximum=high,
            colors=POLLUTION_COLORS, description="Hourly concentration", processing="Native NASA")},
        frame=lambda layer, time: np.full((420, 408), 7.))
    monkeypatch.setattr(routes, "resolve_map_fields", lambda settings, run: fields)
    metadata = client.get("/v1/map/forecast?layer=no2").json()
    assert metadata["layer"]["unit"] == "ppb"
    assert metadata["run_id"] == map_forecast(artifact, state["now"], fields, "no2").run_id
    args = dict(layer="no2", run_id=metadata["run_id"], index=40)
    assert client.get("/v1/map/frame", params=args).status_code == 200
    details = client.get("/v1/map/cell", params={**args,"lat":33.755,"lon":-84.385}).json()
    assert details["layer_value"] == 7 and details["layer_unit"] == "ppb"
    assert details["risk_kind"] == "general"
    args["layer"] = "risk"
    assert client.get("/v1/map/frame", params=args).status_code == 409
    assert client.get("/v1/map/forecast?layer=co").status_code == 404
    assert client.get("/v1/map/forecast?layer=unknown").status_code == 422


def test_personal_maps_require_authentication(published):
    client, artifact, *_ = published
    assert client.get("/v1/map/personal/forecast").status_code == 401
    assert client.get("/v1/map/personal/frame", params=dict(run_id=artifact.digest, index=40)).status_code == 401
    assert client.get("/v1/map/forecast").status_code == 200


def test_personal_scores_match_dashboard_at_each_location_and_baseline(published):
    from app.services.personalization_model import PersonalModel
    from app.services.personal_risk_map import personal_frame_scores
    client, artifact, _, state, _, _ = published
    model = PersonalModel()
    # No reports means no invented personal adjustment.
    np.testing.assert_allclose(personal_frame_scores(artifact, 40, model),
        artifact.general_scores[40][np.ix_(artifact.rows, artifact.columns)], equal_nan=True)
    for _ in range(12):
        model.update(np.array([1., .4, .2, -.1]), .08, 4.)
    values = personal_frame_scores(artifact, 40, model)
    from app.services.personalization_model import personal_scores
    point = personal_scores(artifact.point(33.75, -84.39), artifact.ref, model)["score"][40, 0, 0]
    row, col = int((34.98 - 33.75) / .01), int((-84.39 + 85.6) / .01)
    assert values[row, col] == pytest.approx(point)
    assert np.isnan(values[-1, -1])
    assert not np.allclose(values, artifact.general_scores[40][np.ix_(artifact.rows, artifact.columns)], equal_nan=True)


def test_personal_map_account_revision_isolation_private_headers_and_cache(published, monkeypatch):
    from app.api import risk_map as routes
    from app.auth import get_current_user_id
    from app.dependencies import get_user_session
    from app.services.personalization_model import PersonalModel
    from app.services.risk_map import _images
    client, artifact, _, _, _, _ = published
    users = [uuid4(), uuid4()]
    identity = {"user": users[0], "revision": 1}
    model = PersonalModel()
    monkeypatch.setattr(routes, "prepare_model", lambda *args: (model, identity["revision"], 2))
    monkeypatch.setattr(routes, "saved_model", lambda *args: (model, identity["revision"]))
    client.app.dependency_overrides[get_current_user_id] = lambda: identity["user"]
    client.app.dependency_overrides[get_user_session] = lambda: object()
    metadata = client.get("/v1/map/personal/forecast")
    assert metadata.status_code == 200
    assert metadata.headers["cache-control"] == "private, no-store"
    assert "Authorization" in metadata.headers["vary"].split(", ")
    first = metadata.json()
    assert first["layer"]["id"] == "personal"
    assert first["personalization"]["matched_logs"] == 0
    assert first["personalization"]["pending_logs"] == 2
    args = dict(run_id=first["run_id"], index=40)
    before = list(_images)
    frame = client.get("/v1/map/personal/frame", params=args)
    assert frame.status_code == 200
    assert frame.headers["cache-control"] == "private, no-store"
    assert list(_images) == before  # Private images never enter shared cache.
    assert decode_image(frame.json()["image"])[-1, -1, 3] == 0
    identity["user"] = users[1]
    assert client.get("/v1/map/personal/frame", params=args).status_code == 409
    second = client.get("/v1/map/personal/forecast").json()
    assert second["run_id"] != first["run_id"]
    identity["user"] = users[0]
    identity["revision"] = 2
    assert client.get("/v1/map/personal/frame", params=args).status_code == 409
    revised = client.get("/v1/map/personal/forecast").json()
    assert revised["run_id"] != first["run_id"]
    args["run_id"] = revised["run_id"]
    args["index"] = 9999
    assert client.get("/v1/map/personal/frame", params=args).status_code == 404
    assert client.get("/v1/map/forecast").json()["run_id"] == artifact.digest


def test_cell_matches_raster_grid_score_hour_and_missing_data(published):
    client, artifact, _, _, _, _ = published
    args = dict(run_id=artifact.digest,index=40,lat=33.755,lon=-84.385)
    result = client.get("/v1/map/cell",params=args)
    assert result.status_code == 200
    data = result.json()
    assert data["latitude"] == 33.755 and data["longitude"] == -84.385
    assert data["bounds"] == [-84.39,33.75,-84.38,33.76]
    assert data["risk_score"] == round(float(artifact.general_scores[40,artifact.rows[data["row"]],artifact.columns[data["column"]]]))
    assert data["in_georgia"] and data["forecast_supported"]
    assert data["risk_kind"] == "general" and data["valid_at"].startswith("2026-11-12T01:30")
    assert client.get("/v1/map/cell",params={**args,"run_id":"0"*64}).status_code == 409
    assert client.get("/v1/map/cell",params={**args,"index":0}).status_code == 404
    assert client.get("/v1/map/cell",params={**args,"lon":-81.52}).status_code == 404
    assert client.get("/v1/map/cell",params={**args,"lat":30.78}).status_code == 404
    missing = client.get("/v1/map/cell",params={**args,"lat":31.,"lon":-82.}).json()
    assert missing["risk_score"] is None
    border = client.get("/v1/map/cell",params={**args,"lat":33.7,"lon":-85.55}).json()
    assert not border["in_georgia"]


def test_personal_cell_uses_own_model_and_blocks_anonymous_or_cross_account(published,monkeypatch):
    from app.api import risk_map as routes
    from app.auth import get_current_user_id
    from app.dependencies import get_user_session
    from app.services.personal_risk_map import personal_run_id,personal_frame_scores
    from app.services.personalization_model import PersonalModel
    client,artifact,*_ = published
    user=uuid4();model=PersonalModel()
    for _ in range(5): model.update(np.ones(4),.05,4.)
    args=dict(run_id=personal_run_id(artifact,user,model,1),index=40,lat=33.755,lon=-84.385)
    assert client.get("/v1/map/personal/cell",params=args).status_code == 401
    client.app.dependency_overrides[get_current_user_id] = lambda:user
    client.app.dependency_overrides[get_user_session] = lambda:object()
    monkeypatch.setattr(routes,"saved_model",lambda *args:(model,1))
    result=client.get("/v1/map/personal/cell",params=args)
    assert result.status_code == 200
    data=result.json()
    assert data["risk_kind"] == "personal"
    assert data["risk_score"] == round(float(personal_frame_scores(artifact,40,model)[data["row"],data["column"]]))
    assert result.headers["cache-control"] == "private, no-store"
    client.app.dependency_overrides[get_current_user_id] = lambda:uuid4()
    assert client.get("/v1/map/personal/cell",params=args).status_code == 409
