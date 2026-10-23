# Tests that the health endpoint is working correctly

from fastapi.testclient import TestClient
from legacy.map_prototype.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}