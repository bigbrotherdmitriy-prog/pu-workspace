import os
from unittest.mock import patch

from app.api.telegram import _authorized_admin, _should_prepare_message_replies, _store_ai_message_result
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
