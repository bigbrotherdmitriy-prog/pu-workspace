import os
from unittest.mock import patch
from app.core.notifications import telegram_http_client


def test_telegram_ipv6_client_can_be_constructed():
    with patch.dict(os.environ, {"TELEGRAM_FORCE_IPV6": "true"}):
        client = telegram_http_client()
        try:
            assert client is not None
        finally:
            client.close()
