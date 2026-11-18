"""Regression checks for historical risk, edits, and owner-scoped learning."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from sqlalchemy import func, select

from app.services.personalization_model import PersonalModel, ReferenceStats, exposure, personal_scores
from app.services.risk_artifacts import RiskArtifact, build_artifact, fit_reference
from db.models.application import ExposureSnapshot, PersonalizationRevision
from tests.test_application_api import api, owned_session, BODY


@pytest.fixture
def risk_files(tmp_path):
    lat, lon = np.arange(30.25, 35.01, 0.25), np.arange(-85.5, -80.74, 0.25)
    times = np.datetime64("2026-10-25T09:30") + np.arange(480) * np.timedelta64(1, "h")
    rng = np.random.default_rng(3)
    values = {}
    for name in ("o3", "no2", "so2", "pm25"):
        cube = rng.uniform(0.5, 30, (len(times), len(lat), len(lon))).astype("float32")
        if name != "pm25":
            cube *= 1e-9
        values[name] = (("time", "lat", "lon"), cube, {"units": "ug m-3" if name == "pm25" else "mol mol-1"})
    source = tmp_path / "analysis.nc"
    xr.Dataset(values, coords={"time": times, "lat": lat, "lon": lon},
               attrs={"product": "analysis"}).to_netcdf(source)
    reference = tmp_path / "reference.npz"
    fit_reference([source], reference, provisional=True)
    archive = tmp_path / "history"
    archive.mkdir()
    artifact = archive / "history.npz"
    build_artifact([source], reference, artifact, issued_at="2026-10-29T09:00:00Z",
                   source_run="test", product="analysis")
    return reference, artifact, archive


def test_missing_window_recovers_and_cold_start_matches_general():
    rng = np.random.default_rng(1)
    X = rng.uniform(1, 20, (220, 4, 1, 1))
    X[100, 3] = np.nan
    E = exposure(X)
    assert np.isnan(E[100:172, 3]).all()
    assert np.isfinite(E[172:, 3]).all()
    ref = ReferenceStats.fit(E)
    scores = personal_scores(E, ref, PersonalModel())
    np.testing.assert_allclose(scores["score"], scores["general_score"], equal_nan=True)
    with pytest.raises(ValueError):
        ReferenceStats.fit(exposure(np.ones((220, 4, 1, 1))))


def test_artifact_matches_full_dst_day_and_rejects_gaps(risk_files):
    _, path, _ = risk_files
    artifact = RiskArtifact(path)
    from app.services.personalization_model import general_risk
    with np.load(path, allow_pickle=False) as saved:
        np.testing.assert_allclose(saved["general_scores"], general_risk(artifact.E, artifact.ref).score, equal_nan=True)
    start = datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, 5, tzinfo=timezone.utc)
    assert artifact.day(33.75, -84.39, start, end).shape == (4,)
    selected = np.flatnonzero((artifact.times >= start) & (artifact.times < end))
    assert len(selected) == 25
    artifact.E[selected[0]] = np.nan
    assert artifact.day(33.75, -84.39, start, end) is None


def test_forecast_coordinates_do_not_break_analysis_join(risk_files, tmp_path):
    from app.services.risk_artifacts import assemble

    reference, _, _ = risk_files
    source = reference.parent / "analysis.nc"
    with xr.open_dataset(source) as data:
        latest = data.isel(time=slice(-120, None)).load()
    latest = latest.assign_coords(lead_time=("time", np.arange(120)))
    forecast = tmp_path / "forecast.nc"
    latest.to_netcdf(forecast)
    times, cube, _, _ = assemble([source, forecast])
    assert len(times) == len(cube) == 480


def test_final_logs_rebuild_without_double_counting_and_isolate_users(api, engine, risk_files):
    from dataclasses import replace
    import json

    client, state = api
    _, artifact, archives = risk_files
    dashboard = artifact.parent.parent / "current.json"
    from app.services.geos_dashboard import PublishedForecast

    issued = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    run = PublishedForecast(
        source_run="test", issued_at=issued, times=[issued + timedelta(minutes=30, hours=i) for i in range(120)],
        latitudes=[33.75], longitudes=[-84.5], pm25=[[10.] for _ in range(120)],
        risk_artifact=artifact.name,
    )
    # The endpoint resolves its associated artifact relative to the forecast JSON.
    dashboard = artifact.parent / "current.json"
    dashboard.write_text(run.model_dump_json())
    client.app.state.settings = replace(client.app.state.settings,
        dashboard_forecast_path=str(dashboard), risk_archive_dir=str(archives))

    assert client.put("/v1/symptoms/2026-11-01", json=BODY).status_code == 200
    state["now"] = datetime(2026, 11, 1, 23, tzinfo=timezone.utc)
    query = "/v1/dashboard/personal-risk?lat=33.75&lon=-84.39"
    before = client.get(query)
    assert before.status_code == 200, before.text
    assert before.json()["personalization"]["matched_logs"] == 0
    assert all(day["score"] == day["general_score"] for day in before.json()["days"])

    state["now"] = datetime(2026, 11, 2, 6, tzinfo=timezone.utc)
    first = client.get(query)
    assert first.status_code == 200, first.text
    assert first.json()["personalization"]["matched_logs"] == 1
    revision = first.json()["personalization"]["revision"]
    assert client.get(query).json()["personalization"]["revision"] == revision
    with owned_session(engine, state["user"]) as session:
        row = session.scalar(select(PersonalizationRevision).order_by(PersonalizationRevision.revision.desc()))
        assert row.weights["state"]["n_logs"] == 1
        assert session.scalar(select(func.count()).select_from(ExposureSnapshot)) == 1
    assert client.put("/v1/symptoms/2026-11-01", json={**BODY, "severity": 5, "expected_revision": 1}).status_code == 200
    edited = client.get(query)
    assert edited.json()["personalization"]["matched_logs"] == 1
    assert edited.json()["personalization"]["revision"] > revision
    assert client.get("/v1/symptoms/2026-11-01").json()["exposure_status"] == "matched"

    from uuid import uuid4
    state["user"] = uuid4()
    assert client.get(query).json()["personalization"]["matched_logs"] == 0
