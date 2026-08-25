import os
from unittest.mock import patch

from app.api.telegram import _authorized_admin


def test_only_configured_telegram_admin_is_authorized():
    with patch.dict(os.environ, {"TELEGRAM_ADMIN_USER_ID": "12345"}):
        assert _authorized_admin(12345)
        assert not _authorized_admin(54321)


def test_missing_admin_configuration_denies_access():
    with patch.dict(os.environ, {}, clear=True):
        assert not _authorized_admin(12345)
