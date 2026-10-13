"""One letter from a shared Gmail account lands in exactly one project."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.automations import gmail as auto
from app.models.ai_secretary import Message
from app.models.google_token import GoogleOAuthToken
from app.models.mailbox_identity import MailboxCredentialGeneration
from test_gmail_project_validation import mail, message, sync, world  # noqa: F401 (fixture)


def count(db, project):
    return db.scalar(select(func.count()).select_from(Message).where(Message.project_id == project.id))


def test_letter_held_by_sibling_project_is_skipped(world, monkeypatch):
    db, _user, (alpha, beta) = world
    message(world, project_index=0)                       # Alpha already holds gmail-synthetic-1
    monkeypatch.setattr(auto, "shared_mailbox_sibling_project_ids", lambda db, pid: {alpha.id} if pid == beta.id else set())
    result = sync(world, monkeypatch, mail(), project_index=1)
    assert result["skipped"] == 1 and result["processed"] == 0 and result["failed"] == 0
    assert count(db, beta) == 0 and count(db, alpha) == 1


def test_receipt_shaped_id_in_sibling_also_counts(world, monkeypatch):
    db, _user, (alpha, beta) = world
    message(world, project_index=0, external="gmail-read:conn-1:gmail-synthetic-1")
    monkeypatch.setattr(auto, "shared_mailbox_sibling_project_ids", lambda db, pid: {alpha.id})
    assert sync(world, monkeypatch, mail(), project_index=1)["skipped"] == 1
    assert count(db, beta) == 0


def test_without_siblings_letter_is_ingested(world, monkeypatch):
    db, _user, (alpha, beta) = world
    monkeypatch.setattr(auto, "shared_mailbox_sibling_project_ids", lambda db, pid: set())
    result = sync(world, monkeypatch, mail(), project_index=1)
    assert result["skipped"] == 0 and result["failed"] == 0
    assert count(db, beta) == 1


def test_project_that_already_holds_the_letter_keeps_updating_it(world, monkeypatch):
    db, _user, (alpha, beta) = world
    held = message(world, project_index=0)
    message(world, project_index=1, external="gmail-read:conn-1:gmail-synthetic-1")  # historic duplicate
    monkeypatch.setattr(auto, "shared_mailbox_sibling_project_ids", lambda db, pid: {alpha.id})
    result = sync(world, monkeypatch, mail(), project_index=1)
    # Not short-circuited by the sibling check: the normal "already stored"
    # path ran and refreshed the stored letter's labels.
    db.refresh(held)
    assert result["failed"] == 0 and held.mail_labels_json == '["INBOX"]'
    assert count(db, beta) == 1


def _token(db, project, gen, identity):
    token = GoogleOAuthToken(project_id=project.id, access_token="a", refresh_token="r",
                             token_uri="https://oauth2.googleapis.com/token", scopes="gmail")
    db.add(token); db.flush()
    db.add(MailboxCredentialGeneration(
        id=str(uuid.uuid4()), organization_id=project.organization_id, connection_identity_id=identity,
        generation=gen, binding_epoch=1, google_token_id=token.id, state="active",
        verified_at=datetime.now(timezone.utc)))
    db.flush()
    return token


def test_siblings_are_live_projects_on_the_same_gmail_account(world):
    db, _user, (alpha, beta) = world
    from app.models.project import Project
    gamma = Project(name="Project Gamma", organization_id=alpha.organization_id)
    other = Project(name="Own mailbox", organization_id=alpha.organization_id)
    db.add_all([gamma, other]); db.flush()
    shared, own = str(uuid.uuid4()), str(uuid.uuid4())
    _token(db, alpha, 1, shared); _token(db, beta, 2, shared); _token(db, gamma, 3, shared)
    _token(db, other, 1, own)
    db.commit()
    assert auto.shared_mailbox_sibling_project_ids(db, alpha.id) == {beta.id, gamma.id}
    assert auto.shared_mailbox_sibling_project_ids(db, other.id) == set()
    gamma.archived_at = datetime.now(timezone.utc); db.commit()
    assert auto.shared_mailbox_sibling_project_ids(db, alpha.id) == {beta.id}
