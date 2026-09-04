from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import google_drive
from app.api.google_drive import SCOPES, _fetch_google_credentials


class FakeFlow:
    def __init__(self, granted_scopes):
        self.oauth2session = SimpleNamespace(scope=list(SCOPES))
        self._granted_scopes = granted_scopes
        self.credentials = SimpleNamespace(scopes=granted_scopes)

    def fetch_token(self, *, code):
        assert code == "synthetic-code"
        # Reproduce OAuthLib's old failure mode for an enabled implicit scope
        # comparison.  The production helper must replace it with validation
        # of the actual returned capability set.
        if self.oauth2session.scope is not None:
            raise Warning("scope changed")
        return {"scope": self._granted_scopes}


def test_oauth_reconnect_accepts_google_scope_superset():
    granted = [*SCOPES, "openid"]
    flow = FakeFlow(granted)

    credentials = _fetch_google_credentials(flow, "synthetic-code")

    assert credentials is flow.credentials


def test_oauth_reconnect_rejects_missing_required_scope():
    flow = FakeFlow(SCOPES[:-1])

    with pytest.raises(HTTPException) as exc_info:
        _fetch_google_credentials(flow, "synthetic-code")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Google did not grant all required Workspace permissions"


def test_oauth_reconnect_requests_complete_non_incremental_consent(monkeypatch):
    captured = {}

    class AuthorizationFlow:
        @classmethod
        def from_client_config(cls, config, *, scopes, redirect_uri):
            captured.update(config=config, scopes=scopes, redirect_uri=redirect_uri)
            return cls()

        def authorization_url(self, **kwargs):
            captured["authorization"] = kwargs
            return "https://accounts.example.test/consent", kwargs["state"]

    class Db:
        @staticmethod
        def get(model, project_id):
            return SimpleNamespace(id=project_id)

    monkeypatch.setattr(google_drive, "Flow", AuthorizationFlow)
    monkeypatch.setattr(google_drive, "require_project_role", lambda *args: None)
    monkeypatch.setattr(google_drive, "_make_oauth_state", lambda project_id: "signed-state")
    monkeypatch.setattr(google_drive, "google_config", lambda: ({"web": {}}, "https://callback.example.test"))

    result = google_drive.google_auth(7, Db(), SimpleNamespace(id=1))

    assert result["project_id"] == 7
    assert captured["authorization"] == {
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent",
        "state": "signed-state",
    }
