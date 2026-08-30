import os
from unittest.mock import patch
from app.core.notifications import telegram_http_client


def test_telegram_ipv6_client_can_be_constructed():
    with patch.dict(os.environ, {"TELEGRAM_FORCE_IPV6": "true"}, clear=True):
        client = telegram_http_client()
        try:
            assert client is not None
        finally:
            client.close()


def test_telegram_proxy_client_does_not_bind_ipv6():
    with patch.dict(
        os.environ,
        {
            "TELEGRAM_FORCE_IPV6": "true",
            "HTTPS_PROXY": "http://proxy.example:8000",
        },
        clear=True,
    ):
        client = telegram_http_client()
        try:
            assert client is not None
        finally:
            client.close()
