import os
from unittest.mock import patch

from app.telegram_relay import _force_ipv6, _polling_enabled


def test_polling_enabled_by_default():
    with patch.dict(os.environ, {}, clear=True):
        assert _polling_enabled() is True


def test_polling_can_be_disabled():
    with patch.dict(os.environ, {"TELEGRAM_POLLING_ENABLED": "false"}):
        assert _polling_enabled() is False


def test_ipv6_is_opt_in():
    with patch.dict(os.environ, {}, clear=True):
        assert _force_ipv6() is False
    with patch.dict(os.environ, {"TELEGRAM_FORCE_IPV6": "true"}, clear=True):
        assert _force_ipv6() is True


def test_proxy_takes_precedence_over_forced_ipv6():
    with patch.dict(
        os.environ,
        {
            "TELEGRAM_FORCE_IPV6": "true",
            "HTTPS_PROXY": "http://proxy.example:8000",
        },
        clear=True,
    ):
        assert _force_ipv6() is False
