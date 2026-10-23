from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_works_without_demo_mode_database_or_forecasts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(create_app(Settings(dashboard_demo_enabled=False))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
