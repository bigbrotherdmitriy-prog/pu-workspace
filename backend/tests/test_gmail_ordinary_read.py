import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from google.auth.exceptions import RefreshError
from sqlalchemy import select

from app.api import gmail
from app.integrations.gmail_read import project_gmail_read_status, resolve_project_gmail_read
from app.mailbox_identity.service import MailboxIdentityService
from app.mailbox_identity.runtime import runtime_for_project_connection, runtime_for_message
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxAuthorityState, MailboxCutoverFlags
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.project_contact import ProjectContact
from app.models.task import Task
from app.models.response_draft import ResponseDraft

NOW = datetime.now(timezone.utc)
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@pytest.fixture
def world(db_session, user_factory):
    db, user = db_session, user_factory()
    org = Organization(name="Synthetic ordinary read"); db.add(org); db.flush()
    projects = [Project(name="Project Alpha explicit", organization_id=org.id),
                Project(name="Project Beta explicit", organization_id=org.id)]
    db.add_all(projects); db.flush()
    tokens = []
    for project in projects:
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        token = GoogleOAuthToken(project_id=project.id, access_token="encrypted-synthetic",
            refresh_token="encrypted-synthetic", scopes=SCOPE)
        db.add(token); db.flush(); tokens.append(token)
        identity, mail, _ = MailboxIdentityService().bind_verified_google_subject(db,
            organization_id=org.id, google_token_id=token.id, subject="shared-signed-subject", now=NOW)
    # The closed pilot remains expired and its flags stay off.
    db.add(MailboxAuthorityState(organization_id=org.id, mail_connection_id=mail.id,
        principal_kind="user", principal_id=str(user.id), permissions=["read", "ingest"],
        state="active", authority_version=2, valid_until=NOW - timedelta(days=1)))
    db.commit()
    return SimpleNamespace(db=db, user=user, org=org, a=projects[0], b=projects[1],
                           ta=tokens[0], tb=tokens[1], identity=identity, mail=mail)


def item(id="m1", subject="Project Alpha explicit", body="Synthetic body", sender="sender@example.test"):
    return dict(id=id, threadId="thread1", labelIds=["INBOX", "UNREAD"], payload={
        "mimeType": "text/plain", "headers": [{"name": "Subject", "value": subject},
        {"name": "From", "value": sender}], "body": {
            "data": base64.urlsafe_b64encode(body.encode()).decode()}})


def provider(monkeypatch, world, items, *, after_get=None, error=None):
    calls = []
    class ReadOnlyMessages:
        def list(self, **kwargs):
            calls.append(("list", kwargs))
            if error:
                raise error
            return SimpleNamespace(execute=lambda: {"messages": [{"id": v["id"]} for v in items]})
        def get(self, **kwargs):
            calls.append(("get", kwargs))
            def execute():
                if after_get: after_get()
                return next(v for v in items if v["id"] == kwargs["id"])
            return SimpleNamespace(execute=execute)
    messages = ReadOnlyMessages()
    service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: messages))
    def adapter(token_id, db):
        calls.append(("token", token_id))
        return SimpleNamespace(service=lambda *_: service)
    monkeypatch.setattr(gmail, "google_workspace_for_mailbox", adapter)
    monkeypatch.setattr(gmail, "notify_telegram", lambda *a, **k: pytest.fail("Telegram effect forbidden"))
    monkeypatch.setattr(gmail, "ingest_message", lambda *a, **k: pytest.fail("Analysis/AUTO ingestion forbidden"))
    return calls


def sync(w, project=None):
    return gmail.sync_gmail_read((project or w.a).id, gmail.GmailSyncRequest(), w.db, w.user)


def test_same_account_uses_selected_project_token_without_renewing_closed_pilot(world, monkeypatch):
    w = world
    old_authority = w.db.scalar(select(MailboxAuthorityState))
    expiry = old_authority.valid_until
    calls = provider(monkeypatch, w, [item()])
    result = sync(w)
    assert result["processed"] == 1 and result["auto_enabled"] is False
    assert calls[0] == ("token", w.ta.id) and w.ta.id != w.tb.id
    assert old_authority.authority_version == 2 and old_authority.valid_until == expiry
    assert all(not row.pilot_write and not row.primary_read and not row.actions
               for row in w.db.scalars(select(MailboxCutoverFlags)))
    assert w.db.scalar(select(Task)) is None and w.db.scalar(select(ResponseDraft)) is None
    row = w.db.scalar(select(Message))
    assert row.project_id == w.a.id and row.context_confirmed is False
    assert row.context_confirmed_by_user_id is None and row.source_reference_id is None
    assert row.mail_connection_id is None and row.analysis_required is True
    assert json.loads(row.mail_labels_json) == ["INBOX", "UNREAD"]
    assert project_gmail_read_status(w.db, w.a.id)["connected"] is True
    assert project_gmail_read_status(w.db, w.b.id)["connected"] is False


def test_repeat_is_idempotent_and_other_project_never_moves_receipt(world, monkeypatch):
    w = world
    provider(monkeypatch, w, [item()])
    assert sync(w)["processed"] == 1
    row = w.db.scalar(select(Message)); row.summary = "owner annotation"; w.db.commit()
    assert sync(w)["skipped"] == 1
    assert sync(w, w.b)["excluded"] == 1
    assert len(list(w.db.scalars(select(Message)))) == 1 and row.summary == "owner annotation"


def test_unknown_other_and_ambiguous_project_mail_are_not_imported(world, monkeypatch):
    w = world
    provider(monkeypatch, w, [item("m1", "Unknown"), item("m2", w.b.name),
                           item("m3", f"{w.a.name}; {w.b.name}")])
    result = sync(w)
    assert result["excluded"] == 3 and result["processed"] == 0
    assert w.db.scalar(select(Message)) is None


def test_confirmed_mailbox_contact_routes_only_to_its_project(world, monkeypatch):
    w = world
    w.db.add(ProjectContact(organization_id=w.org.id, project_id=w.a.id,
        created_by_user_id=w.user.id,
        mail_connection_id=w.mail.id, normalized_email="sender@example.test",
        email="sender@example.test", name="Synthetic", active=True, confirmed=True, resolution_state="confirmed"))
    w.db.commit()
    provider(monkeypatch, w, [item(subject="Unknown")])
    assert sync(w)["processed"] == 1


@pytest.mark.parametrize("change", ["token", "identity", "mail", "scope", "member", "epoch"])
def test_stale_own_connection_denied_after_network_before_receipts(world, monkeypatch, change):
    w = world
    def invalidate():
        if change == "token": w.ta.credential_generation += 1
        elif change == "identity": w.identity.state = "revoked"
        elif change == "mail": w.mail.state = "revoked"
        elif change == "scope": w.ta.scopes = ""
        elif change == "member":
            w.db.delete(w.db.scalar(select(ProjectMember).where(ProjectMember.project_id == w.a.id)))
        elif change == "epoch": w.identity.binding_epoch += 1
        w.db.commit()
    provider(monkeypatch, w, [item()], after_get=invalidate)
    with pytest.raises(HTTPException) as exc: sync(w)
    assert exc.value.status_code in {403, 409}
    assert w.db.scalar(select(Message)) is None


def test_neighbor_reconnect_during_read_does_not_change_own_pins(world, monkeypatch):
    w = world
    def rotate_neighbor():
        w.tb.credential_generation += 1
        MailboxIdentityService().bind_verified_google_subject(w.db, organization_id=w.org.id,
            google_token_id=w.tb.id, subject="shared-signed-subject", now=NOW + timedelta(seconds=1))
        w.db.commit()
    provider(monkeypatch, w, [item()], after_get=rotate_neighbor)
    assert sync(w)["processed"] == 1


def test_explicit_ordinary_receipt_cannot_use_legacy_action_fallback(world, monkeypatch):
    w = world
    provider(monkeypatch, w, [item()]); sync(w)
    row = w.db.scalar(select(Message))
    with pytest.raises(ValueError): runtime_for_message(w.db, row, actor=w.user, action=True)
    from app.api.mail import move_mail_message, MailMoveRequest
    with pytest.raises(HTTPException) as exc:
        move_mail_message(row.id, MailMoveRequest(destination="trash"), w.db, w.user)
    assert exc.value.status_code == 409


def test_unverified_credentials_denied_before_provider(world, monkeypatch):
    w = world
    w.identity.state = "unverified"; w.db.commit()
    calls = provider(monkeypatch, w, [item()])
    with pytest.raises(HTTPException): sync(w)
    assert not calls


def test_status_never_claims_ready_from_oauth_alone_and_hides_provider_error(world, monkeypatch):
    w = world
    assert project_gmail_read_status(w.db, w.a.id)["state"] == "configured_not_checked"
    provider(monkeypatch, w, [], error=RefreshError("SECRET_TOKEN synthetic private error"))
    with pytest.raises(HTTPException) as exc: sync(w)
    assert exc.value.detail == "gmail_provider_authorization_failed"
    status = project_gmail_read_status(w.db, w.a.id)
    assert not status["available"] and not status["connected"]
    assert all("SECRET_TOKEN" not in (row.details or "") for row in w.db.scalars(select(AuditLog)))
    w.ta.credential_generation += 1; w.db.commit()
    assert project_gmail_read_status(w.db, w.a.id)["state"] == "configured_not_checked"


def test_viewer_cannot_sync_and_revocation_is_not_pilot_fallback(world, monkeypatch):
    w = world
    member = w.db.scalar(select(ProjectMember).where(ProjectMember.project_id == w.a.id))
    member.role = "viewer"; w.db.commit()
    calls = provider(monkeypatch, w, [item()])
    with pytest.raises(HTTPException) as exc: sync(w)
    assert exc.value.status_code == 403 and calls == []


def test_pilot_resolver_stays_fail_closed_on_shared_generation(world):
    w = world
    w.ta.scopes = SCOPE
    # Force an old participating cohort: the old route must NOT adopt the new
    # ordinary project's read authorization.
    from app.models.mailbox_identity import MailboxProjectCohort
    cohort = w.db.scalar(select(MailboxProjectCohort).where(MailboxProjectCohort.project_id == w.a.id))
    cohort.enabled = True; w.db.commit()
    with pytest.raises(ValueError): runtime_for_project_connection(w.db, w.a.id)
    assert resolve_project_gmail_read(w.db, w.a.id).token_id == w.ta.id


def test_http_read_endpoint_validates_limits_before_provider(world, monkeypatch):
    from fastapi import FastAPI
    from app.core.auth import require_user
    from app.database import get_db
    w = world
    calls = provider(monkeypatch, w, [])
    app = FastAPI(); app.include_router(gmail.router)
    app.dependency_overrides[get_db] = lambda: w.db
    app.dependency_overrides[require_user] = lambda: w.user
    import asyncio
    import httpx
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invalid = await client.post(f"/projects/{w.a.id}/gmail/read-sync", json={"max_results": 101})
            assert invalid.status_code == 422
    asyncio.run(request())
    assert calls == []
