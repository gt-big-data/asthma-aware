"""Hourly dashboard scores preserve aggregation, personalization and missing hours."""
from datetime import timedelta

import numpy as np

from app.services.personalization_model import PersonalModel, personal_scores
from app.services.risk_dashboard import risk_dashboard
from tests.test_risk_map import published


def test_hourly_route_matches_daily_maxima_and_remaining_horizon(published):
    client, _, _, _, _, _ = published
    response = client.get("/v1/dashboard/risk?lat=33.75&lon=-84.39")
    assert response.status_code == 200
    data = response.json()
    assert len(data["hourly"]) == 88
    assert data["hourly"][0]["valid_at"] == "2026-11-11T17:30:00Z"
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for day in data["days"]:
        values = [hour["score"] for hour in data["hourly"] if hour["score"] is not None
                  and datetime.fromisoformat(hour["valid_at"].replace("Z", "+00:00")).astimezone(ZoneInfo(data["timezone"])).date().isoformat() == day["date"]]
        assert day["score"] == (max(values) if values else None)
        assert day["available_hours"] == len(values)


def test_hourly_personal_score_uses_same_model_and_preserves_missing_slots(published):
    _, artifact, run, state, _, _ = published
    # A confident learned baseline makes this distinct from population scores.
    P = np.eye(5) * 1e8
    model = PersonalModel(P=P, h=P @ np.array([4., 0, 0, 0, 0]), n_logs=25)
    row, column = artifact.rows[123], artifact.columns[121]
    artifact.E[35, :, row, column] = np.nan
    result = risk_dashboard(artifact, 33.75, -84.39, state["now"], model=model)
    selected = artifact.point(33.75, -84.39)[32:]
    expected = personal_scores(selected, artifact.ref, model)["score"][:, 0, 0]
    assert len(result.hourly) == len(expected)
    for hour, value in zip(result.hourly, expected):
        assert hour.score == (round(float(value)) if np.isfinite(value) else None)
    assert result.hourly[3].score is None and result.hourly[3].general_score is None
    assert any(hour.score != hour.general_score for hour in result.hourly if hour.score is not None)
    assert result.hourly[-1].valid_at < run.issued_at + timedelta(hours=120)


def test_hourly_missing_location_stays_missing_and_expiry_is_rejected(published):
    client, _, run, state, _, _ = published
    response = client.get("/v1/dashboard/risk?lat=31&lon=-82")
    assert response.status_code == 200
    assert len(response.json()["hourly"]) == 88
    assert all(hour["score"] is None for hour in response.json()["hourly"])
    state["now"] = run.issued_at + timedelta(hours=120)
    assert client.get("/v1/dashboard/risk?lat=33.75&lon=-84.39").status_code == 503
