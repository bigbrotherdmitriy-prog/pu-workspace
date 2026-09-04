from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import google_drive
from app.api.google_drive import SCOPES, _fetch_google_credentials
from app.models.google_token import GoogleOAuthToken
from app.models.organization_contract import Organization
from app.models.project import Project


class FakeFlow:
    def __init__(self, granted_scopes):
        self.oauth2session = SimpleNamespace(scope=list(SCOPES))
        self._granted_scopes = granted_scopes
        self.credentials = SimpleNamespace(scopes=granted_scopes)

    def fetch_token(self, *, code):
        self.received_code = code
        if self.oauth2session.scope is not None:
            raise Warning("scope changed")
        return {"scope": self._granted_scopes}


def test_oauth_reconnect_accepts_google_scope_superset():
    flow = FakeFlow([*SCOPES, "email", "profile"])

    credentials = _fetch_google_credentials(flow, "synthetic-code")

    assert credentials is flow.credentials


def test_oauth_reconnect_accepts_space_delimited_scope_superset():
    flow = FakeFlow([*SCOPES, "email"])
    flow._granted_scopes = " ".join([*SCOPES, "email"])

    assert _fetch_google_credentials(flow, "synthetic-code") is flow.credentials


def test_oauth_reconnect_rejects_missing_required_scope_without_leaking_values():
    secret_code = "synthetic-secret-code"
    flow = FakeFlow(SCOPES[:-1])

    with pytest.raises(HTTPException) as exc_info:
        _fetch_google_credentials(flow, secret_code)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Google did not grant all required Workspace permissions"
    assert flow.received_code == secret_code
    assert secret_code not in str(exc_info.value.detail)
    assert SCOPES[-1] not in str(exc_info.value.detail)


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

    role_checks = []
    monkeypatch.setattr(google_drive, "Flow", AuthorizationFlow)
    monkeypatch.setattr(
        google_drive,
        "require_project_role",
        lambda db, user, project_id, role: role_checks.append((project_id, role)),
    )
    monkeypatch.setattr(google_drive, "_make_oauth_state", lambda project_id: "signed-state")
    monkeypatch.setattr(
        google_drive,
        "google_config",
        lambda: ({"web": {}}, "https://callback.example.test"),
    )

    result = google_drive.google_auth(7, Db(), SimpleNamespace(id=1))

    assert result["project_id"] == 7
    assert role_checks == [(7, "manager")]
    assert captured["scopes"] == SCOPES
    assert captured["authorization"] == {
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent",
        "state": "signed-state",
    }


def test_callback_rejects_incomplete_scope_before_oidc_or_token_persistence(
    db_session,
    monkeypatch,
):
    organization = Organization(name="Synthetic OAuth organization")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Synthetic OAuth project", organization_id=organization.id)
    db_session.add(project)
    db_session.commit()

    credentials = SimpleNamespace(
        id_token="synthetic-id-token",
        token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        token_uri="https://oauth2.example.test/token",
        scopes=SCOPES[:-1],
    )
    flow = FakeFlow(SCOPES[:-1])
    flow.credentials = credentials
    oidc_calls = []

    monkeypatch.setattr(google_drive, "_project_from_oauth_state", lambda state: project.id)
    monkeypatch.setattr(
        google_drive,
        "google_config",
        lambda: ({"web": {"client_id": "synthetic-client"}}, "https://callback.example.test"),
    )
    monkeypatch.setattr(google_drive.Flow, "from_client_config", lambda *args, **kwargs: flow)
    monkeypatch.setattr(
        google_drive,
        "verified_google_subject",
        lambda *args, **kwargs: oidc_calls.append(True),
    )

    with pytest.raises(HTTPException) as exc_info:
        google_drive.google_callback("synthetic-code", "signed-state", db_session)

    assert exc_info.value.status_code == 400
    assert oidc_calls == []
    assert db_session.scalar(select(GoogleOAuthToken)) is None
