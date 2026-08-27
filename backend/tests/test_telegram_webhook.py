import os
from unittest.mock import patch

from app.api.telegram import _authorized_admin, _should_prepare_message_replies


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
