import os
from unittest.mock import patch

from app.api.telegram import (
    _authorized_admin,
    _project_choices_message,
    _reanalyze_existing_document,
    _should_prepare_message_replies,
    _store_ai_message_result,
)
from app.models.audit_log import AuditLog
from app.models.ai_secretary import Message
from app.models.organization_contract import Organization
from app.models.project import Project
from app.organizer_engine.types import DriveFile


def test_only_configured_telegram_admin_is_authorized():
    with patch.dict(os.environ, {"TELEGRAM_ADMIN_USER_ID": "12345"}):
        assert _authorized_admin(12345)
        assert not _authorized_admin(54321)


def test_missing_admin_configuration_denies_access():
    with patch.dict(os.environ, {}, clear=True):
        assert not _authorized_admin(12345)


def test_forwarded_group_message_prepares_replies():
    message = {"chat": {"type": "group"}, "forward_origin": {"type": "user"}}
    assert _should_prepare_message_replies(message, "Просьба уточнить данные")


def test_regular_group_message_does_not_trigger_reply_spam():
    message = {"chat": {"type": "group"}}
    assert not _should_prepare_message_replies(message, "Коллеги, доброе утро")


def test_private_message_prepares_replies_but_commands_do_not():
    message = {"chat": {"type": "private"}}
    assert _should_prepare_message_replies(message, "Подготовь ответ")
    assert not _should_prepare_message_replies(message, "/tasks")


def test_project_choices_show_connect_commands(db_session):
    organization = Organization(name="Test")
    db_session.add(organization); db_session.flush()
    db_session.add_all([
        Project(organization_id=organization.id, name="Первый проект"),
        Project(organization_id=organization.id, name="Второй проект"),
    ])
    db_session.flush()
    text = _project_choices_message(db_session)
    assert "Выберите проект" in text
    assert "— Второй проект" in text
    assert text.count("/connect ") == 2


def test_ai_message_result_is_kept_as_reviewable_draft(db_session, user_factory):
    user = user_factory()
    organization = Organization(name="Test")
    db_session.add(organization); db_session.flush()
    project = Project(organization_id=organization.id, name="Project")
    db_session.add(project); db_session.flush()
    message = Message(
        organization_id=organization.id, project_id=project.id,
        created_by_user_id=user.id, source_type="telegram", source_external_id="telegram:1:2",
        source_name="Telegram", content="Просьба ответить", summary="Анализируется",
        context_evidence="test", context_confirmed=True, status="ready",
    )
    db_session.add(message); db_session.flush()
    drafts = _store_ai_message_result(
        db_session, message, [],
        {"message_summary": "Нужно подтвердить срок", "recommended_action": "Ответить сегодня",
         "business_reply": "Добрый день! Срок подтверждаем.", "confidence": "high"},
        user, DriveFile(id="message:1", name="Telegram", mime_type="text/plain", parent_id="telegram", content_text=message.content),
    )
    db_session.flush()
    assert len(drafts) == 1
    assert drafts[0].message_id == message.id
    assert "Срок подтверждаем" in drafts[0].body
    assert "Нужно подтвердить срок" in message.summary


def test_duplicate_document_can_be_reanalyzed_without_duplicate_business_items(
    db_session, user_factory, monkeypatch,
):
    user = user_factory()
    organization = Organization(name="Test")
    db_session.add(organization); db_session.flush()
    project = Project(organization_id=organization.id, name="Project")
    db_session.add(project); db_session.flush()
    message = Message(
        organization_id=organization.id, project_id=project.id,
        created_by_user_id=user.id, source_type="telegram",
        source_external_id="telegram-file:1:stable-file", source_name="stages.xlsx",
        content="Stage 1 deadline 2027-01-10", summary="Local summary",
        context_evidence="test", context_confirmed=True, status="ready",
    )
    db_session.add(message); db_session.flush()

    class Provider:
        provider = "gemini"
        model = "gemini-3.8-flash"

        def health(self):
            return type("Health", (), {"ready": True})()

        def analyze_document(self, text, filename):
            raise AssertionError("cached result should be used")

    result = {
        "document_type": "schedule", "executive_summary": "Updated analysis",
        "parties": [], "contract_references": [], "amounts": [], "dates": [],
        "obligations": [], "risks": [], "inconsistencies": [], "missing_data": [],
        "recommended_actions": [], "draft_reply": "", "confidence": "medium",
    }
    monkeypatch.setattr("app.api.telegram.configured_ai_provider", lambda: Provider())
    monkeypatch.setattr("app.api.telegram.prepare_external_ai_text", lambda db, project_id, text: (text, "external_allowed"))
    monkeypatch.setattr("app.api.telegram.policy_for_project", lambda db, project_id: None)
    monkeypatch.setattr("app.api.telegram.cached_ai_result", lambda *args, **kwargs: (result, True))

    response = _reanalyze_existing_document(
        db_session, message,
        refreshed_content="Stage 1 starts 2027-01-01 and ends 2027-02-01",
        refreshed_source_name="updated-stages.xlsx",
    )

    assert "Updated analysis" in response
    assert "Повторные задачи, риски и документы не создавались" in response
    assert "Updated analysis" in message.summary
    assert message.content == "Stage 1 starts 2027-01-01 and ends 2027-02-01"
    assert message.source_name == "updated-stages.xlsx"
    audit = db_session.query(AuditLog).filter_by(action="external_ai_reanalysis", entity_id=message.id).one()
    assert "duplicates_created=false" in audit.details
