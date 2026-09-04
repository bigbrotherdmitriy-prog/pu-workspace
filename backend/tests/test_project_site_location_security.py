from fastapi.testclient import TestClient

from app.main import app


def test_first_party_geolocation_is_enabled_without_enabling_other_sensors():
    response = TestClient(app).get("/api/status")

    assert response.status_code == 200
    policy = response.headers["Permissions-Policy"]
    assert "geolocation=(self)" in policy
    assert "camera=()" in policy
    assert "microphone=()" in policy
