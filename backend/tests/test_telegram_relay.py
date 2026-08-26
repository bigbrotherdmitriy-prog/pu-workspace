import os
from unittest.mock import patch

from app.telegram_relay import _polling_enabled


def test_polling_enabled_by_default():
    with patch.dict(os.environ, {}, clear=True):
        assert _polling_enabled() is True


def test_polling_can_be_disabled():
    with patch.dict(os.environ, {"TELEGRAM_POLLING_ENABLED": "false"}):
        assert _polling_enabled() is False
