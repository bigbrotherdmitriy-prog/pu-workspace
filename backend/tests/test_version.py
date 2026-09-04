from app.main import APP_VERSION, api_status, app


def test_public_status_matches_openapi_version():
    assert api_status()["version"] == APP_VERSION
    assert app.version == APP_VERSION


def test_status_exposes_deployed_release(monkeypatch):
    monkeypatch.setenv("PU_RELEASE_REVISION", "release-test-42")
    assert api_status()["release"] == "release-test-42"
