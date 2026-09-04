import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import ai_secretary as ai, gmail, project_contacts as contacts
from app.models.ai_secretary import Message
from app.models.organization_contract import Organization, Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.project_contact import ProjectContact
from app.models.task import Task
from app.models.task_completion_suggestion import TaskCompletionSuggestion
from app.integrations.contracts import AIProviderAdapter
from app.integrations import ai as ai_adapter


@pytest.fixture
def world(db_session, user_factory, monkeypatch):
    db = db_session
    user = user_factory()
    org = Organization(name="Synthetic Organization")
    db.add(org); db.flush()
    projects = [Project(name=name, organization_id=org.id) for name in ("Project Alpha", "Project Beta")]
    db.add_all(projects); db.flush()
    for project in projects:
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="editor"))
    db.commit()
    # Exercise routing/persistence, never external AI, Telegram or engines.
    monkeypatch.setattr(ai, "create_tasks_from_files", lambda *a, **k: [])
    monkeypatch.setattr(ai, "create_response_drafts", lambda *a, **k: [])
    monkeypatch.setattr(gmail, "create_response_drafts", lambda *a, **k: [])
    monkeypatch.setattr(ai, "create_governance_items", lambda *a, **k: ([], []))
    monkeypatch.setattr(ai, "brief_summary", lambda *a: "Synthetic summary")
    monkeypatch.setattr(ai, "configured_action_adapter", lambda *a: SimpleNamespace(provider="test"))
    monkeypatch.setattr(gmail, "notify_telegram", lambda *a: None)
    provider = Mock(spec=AIProviderAdapter)
    provider.analyze_message.return_value = {}
    provider.analyze_document.return_value = {}
    monkeypatch.setattr(ai_adapter, "configured_ai_provider", lambda: provider)
    return db, user, projects


def mail(text="Unclassified correspondence", subject="Question", sender="client@example.test", outgoing=False):
    return {"id": "gmail-synthetic-1", "threadId": "thread-1", "labelIds": ["SENT" if outgoing else "INBOX"],
            "payload": {"mimeType": "text/plain", "headers": [
                {"name": "Subject", "value": subject}, {"name": "From", "value": sender},
                {"name": "To", "value": "client@example.test"}],
                "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}}


def sync(world, monkeypatch, item, project_index=0):
    db, user, projects = world
    calls = []
    class FakeGmail:
        def users(self): return self
        def messages(self): return self
        def list(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda: {"messages": [{"id": item["id"]}]})
        def get(self, **kwargs): return SimpleNamespace(execute=lambda: item)
    monkeypatch.setattr(gmail, "google_workspace_for_project", lambda *a: SimpleNamespace(service=lambda *a: FakeGmail()))
    result = gmail.sync_gmail_project(projects[project_index].id, db, user, query="newer_than:7d", max_results=25)
    assert calls == [{"userId": "me", "q": "newer_than:7d", "maxResults": 25}]
    return result


def contact(world, *, confirmed=True, active=True):
    db, user, projects = world
    row = ProjectContact(organization_id=projects[0].organization_id, project_id=projects[0].id,
        created_by_user_id=user.id, name="Client", email="client@example.test", normalized_email="client@example.test",
        confirmed=confirmed, active=active, source="manual" if confirmed else "gmail")
    db.add(row); db.commit()
    return row


def message(world, project_index=0, external="gmail-synthetic-1", confirmed=True):
    db, user, projects = world
    p = projects[project_index]
    row = Message(organization_id=p.organization_id, project_id=p.id, created_by_user_id=user.id,
        source_type="email", source_external_id=external, source_name="Synthetic", content="Synthetic",
        summary="Synthetic", context_confidence=1 if confirmed else .4, context_evidence="Human choice",
        context_confirmed=confirmed, status="ready", attachments_json="[]")
    db.add(row); db.commit()
    return row


def test_semantic_project_confidence_survives_ingest(world, monkeypatch):
    assert sync(world, monkeypatch, mail(subject="Project Beta"))["processed"] == 1
    row = world[0].scalar(select(Message))
    assert row.project_id == world[2][1].id
    assert row.context_confirmed is True
    assert "Project Beta" in row.context_evidence


def test_contact_conflicting_with_explicit_project_requires_review(world, monkeypatch):
    contact(world)
    assert sync(world, monkeypatch, mail(subject="Project Beta"))["processed"] == 1
    row = world[0].scalar(select(Message))
    assert row.context_confirmed is False
    assert row.status == "needs_context_confirmation"
    assert str(world[2][0].id) in row.context_evidence and str(world[2][1].id) in row.context_evidence


def test_ambiguous_projects_not_confirmed_by_fallback_contract(world, monkeypatch):
    db, _, projects = world
    db.add(Contract(project_id=projects[0].id, number="C-100", title="Synthetic contract", status="active")); db.commit()
    sync(world, monkeypatch, mail(text="Project Alpha Project Beta C-100"))
    row = db.scalar(select(Message))
    assert row.context_confirmed is False
    assert row.contract_id is None


def test_discovery_does_not_move_or_reactivate_contact(world):
    db, user, projects = world
    row = contact(world, confirmed=False, active=False)
    contacts.discover_contact_from_message(db, projects[1].id, " Client <CLIENT@example.test> ", "New text", user)
    assert row.project_id == projects[0].id
    assert row.active is False


def test_moving_contact_clears_previous_project_contract(world):
    db, user, projects = world
    row = contact(world)
    contract_row = Contract(project_id=projects[0].id, number="C-100", title="Synthetic", status="active")
    db.add(contract_row); db.flush(); row.contract_id = contract_row.id; db.commit()
    contacts.update_contact(row.id, contacts.ContactUpdate(project_id=projects[1].id), db, user)
    assert row.contract_id is None


def test_sync_does_not_backfill_inaccessible_message(world, monkeypatch):
    db, user, projects = world
    row = message(world, 1)
    membership = db.scalar(select(ProjectMember).where(ProjectMember.project_id == projects[1].id))
    db.delete(membership); db.commit()
    item = mail()
    item["payload"]["parts"] = [{"filename": "synthetic.txt", "body": {"attachmentId": "fake", "size": 1}}]
    result = sync(world, monkeypatch, item)
    assert result["failed"] == 1
    assert row.attachments_json == "[]"


def test_ingest_dedup_does_not_return_inaccessible_message(world):
    db, user, projects = world
    message(world, 1)
    membership = db.scalar(select(ProjectMember).where(ProjectMember.project_id == projects[1].id))
    db.delete(membership); db.commit()
    with pytest.raises(HTTPException) as exc:
        ai.ingest_message(ai.IncomingMessage(project_id=projects[0].id, source_type="email",
            source_external_id="gmail-synthetic-1", source_name="Synthetic", content="Synthetic"), db, user)
    assert exc.value.status_code == 403


def test_stale_completion_cannot_close_task_in_old_project(world):
    db, user, projects = world
    row = message(world, 1)
    task = Task(project_id=projects[0].id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="Synthetic", source_file_id="fake", source_file_name="fake", source_excerpt="fake",
        source_excerpt_hash="fake", confidence=.9, status="assigned")
    db.add(task); db.flush()
    suggestion = TaskCompletionSuggestion(project_id=projects[0].id, message_id=row.id, task_id=task.id,
        confidence=.9, evidence="Synthetic", status="proposed")
    db.add(suggestion); db.commit()
    with pytest.raises(HTTPException) as exc:
        ai.review_completion_suggestion(row.id, suggestion.id, ai.CompletionReview(status="confirmed"), db, user)
    assert exc.value.status_code == 409
    assert task.status == "assigned"


def test_contract_number_is_not_matched_inside_other_number(world, monkeypatch):
    db, _, projects = world
    db.add(Contract(project_id=projects[0].id, number="12", title="Synthetic", status="active")); db.commit()
    sync(world, monkeypatch, mail(text="Invoice 3124 for review"))
    assert db.scalar(select(Message)).context_confirmed is False


def test_company_domain_alone_does_not_confirm_project(world, monkeypatch):
    db, _, projects = world
    db.add(Contract(project_id=projects[0].id, number="C-900", title="Synthetic", counterparty="example.test", status="active")); db.commit()
    sync(world, monkeypatch, mail(text="Contact: client@example.test"))
    assert db.scalar(select(Message)).context_confirmed is False


def test_multi_recipient_mail_does_not_route_by_first_contact(world, monkeypatch):
    contact(world)
    item = mail(outgoing=True)
    item["payload"]["headers"][-1]["value"] = "client@example.test, second@example.test"
    sync(world, monkeypatch, item)
    assert world[0].scalar(select(Message)).context_confirmed is False


def test_repeat_sync_preserves_manual_project_contract_and_status(world, monkeypatch):
    db, user, projects = world
    sync(world, monkeypatch, mail(subject="Project Alpha"))
    row = db.scalar(select(Message))
    contract_row = Contract(project_id=projects[1].id, number="C-200", title="Synthetic", status="active")
    db.add(contract_row); db.commit()
    ai.confirm_context(row.id, ai.ContextConfirmation(project_id=projects[1].id, contract_id=contract_row.id), db, user)
    row.status = "in_progress"; db.commit()
    before = (row.project_id, row.contract_id, row.context_evidence, row.status)
    assert sync(world, monkeypatch, mail(subject="Project Alpha"))["skipped"] == 1
    assert (row.project_id, row.contract_id, row.context_evidence, row.status) == before
    assert len(list(db.scalars(select(Message)))) == 1


def test_same_domain_different_sender_not_a_contact_route(world, monkeypatch):
    contact(world)
    sync(world, monkeypatch, mail(sender="other@example.test"))
    row = world[0].scalar(select(Message))
    assert row.context_confirmed is False and row.contract_id is None


def test_contact_normalization_no_duplicates_or_company_merge(world):
    db, user, projects = world
    first = contacts.discover_contact_from_message(db, projects[0].id, " Client <SALES@example.test> ", "Synthetic", user)
    second = contacts.discover_contact_from_message(db, projects[0].id, "sales@example.test", "Synthetic", user)
    other = contacts.discover_contact_from_message(db, projects[0].id, "other@example.test", "Synthetic", user)
    assert first.id == second.id and other.id != first.id
    assert len(list(db.scalars(select(Organization)))) == 1
    assert first.company == "example.test"  # display hint, not a company FK


def test_confirmed_contact_fields_survive_discovery(world):
    db, user, projects = world
    row = contact(world)
    row.company = "Manually named company"; row.company_activity = "Manual note"; db.commit()
    contacts.discover_contact_from_message(db, projects[1].id, "Changed <client@example.test>", "Replacement", user)
    assert (row.project_id, row.name, row.company_activity, row.company) == (projects[0].id, "Client", "Manual note", "Manually named company")


def test_duplicate_email_in_second_project_requires_explicit_resolution(world):
    db, user, projects = world
    contact(world)
    with pytest.raises(HTTPException) as exc:
        contacts.create_contact(contacts.ContactCreate(project_id=projects[1].id, name="Same client", email=" CLIENT@example.test "), db, user)
    assert exc.value.status_code == 409


def test_semantic_routing_never_uses_inaccessible_project(world, monkeypatch):
    db, _, projects = world
    db.delete(db.scalar(select(ProjectMember).where(ProjectMember.project_id == projects[1].id))); db.commit()
    sync(world, monkeypatch, mail(subject="Project Beta"))
    row = db.scalar(select(Message))
    assert row.project_id == projects[0].id and row.context_confirmed is False


def test_manual_source_id_does_not_shadow_gmail_message(world, monkeypatch):
    db, _, _ = world
    row = message(world); row.source_type = "manual"; db.commit()
    assert sync(world, monkeypatch, mail(subject="Project Beta"))["processed"] == 1
    assert len(list(db.scalars(select(Message)))) == 2


def test_duplicate_from_other_organization_fails_closed(world, monkeypatch):
    db, user, projects = world
    row = message(world)
    org = Organization(name="Different organization"); db.add(org); db.flush()
    row.organization_id = org.id; db.commit()
    user.is_admin = True; db.commit()
    assert sync(world, monkeypatch, mail())["failed"] == 1
    assert row.organization_id == org.id and row.context_evidence == "Human choice"


def test_reply_thread_without_mailbox_identity_stays_unconfirmed(world, monkeypatch):
    db, _, projects = world
    previous = message(world, external="old-outgoing")
    previous.source_type = "email_outgoing"; previous.source_thread_id = "thread-1"; db.commit()
    item = mail()
    item["payload"]["headers"] += [{"name": "In-Reply-To", "value": "<old@example.test>"},
                                    {"name": "References", "value": "<old@example.test>"}]
    sync(world, monkeypatch, item, project_index=1)
    row = db.scalar(select(Message).where(Message.source_external_id == item["id"]))
    assert row.context_confirmed is False
    assert row.status == "needs_context_confirmation"
    assert previous.project_id == projects[0].id


def test_outgoing_does_not_complete_task_without_human_review(world, monkeypatch):
    db, user, projects = world
    task = Task(project_id=projects[0].id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="Направить исправленный акт", description="Исправленный акт направили",
        source_file_id="fake", source_file_name="fake", source_excerpt="fake", source_excerpt_hash="fake",
        confidence=.9, status="assigned")
    db.add(task); db.commit()
    sync(world, monkeypatch, mail(text="Исправленный акт направили, готово", subject="Project Alpha", outgoing=True))
    suggestion = db.scalar(select(TaskCompletionSuggestion))
    assert suggestion is not None and suggestion.status == "proposed"
    assert task.status == "assigned"
    ai.review_completion_suggestion(suggestion.message_id, suggestion.id, ai.CompletionReview(status="confirmed"), db, user)
    assert task.status == "completed"
    assert ai.review_completion_suggestion(suggestion.message_id, suggestion.id, ai.CompletionReview(status="confirmed"), db, user)["already_reviewed"]


def test_unconfirmed_outgoing_has_no_completion_suggestions(world, monkeypatch):
    db, user, projects = world
    task = Task(project_id=projects[0].id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="Исправленный акт направили", source_file_id="fake", source_file_name="fake",
        source_excerpt="fake", source_excerpt_hash="fake", confidence=.9, status="assigned")
    db.add(task); db.commit()
    sync(world, monkeypatch, mail(text="Исправленный акт направили готово", outgoing=True))
    assert not list(db.scalars(select(TaskCompletionSuggestion)))
    assert task.status == "assigned"


def test_unknown_sender_does_not_create_confirmed_contact(world, monkeypatch):
    sync(world, monkeypatch, mail())
    assert not list(world[0].scalars(select(ProjectContact)))
