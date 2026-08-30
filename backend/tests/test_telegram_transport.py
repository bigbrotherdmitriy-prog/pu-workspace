import os
from unittest.mock import Mock, patch
from app.core.notifications import telegram_http_client


def test_telegram_ipv6_client_can_be_constructed():
    transport = Mock()
    client = Mock()
    with (
        patch.dict(os.environ, {"TELEGRAM_FORCE_IPV6": "true"}, clear=True),
        patch("app.integrations.telegram.httpx.HTTPTransport", return_value=transport) as transport_factory,
        patch("app.integrations.telegram.httpx.Client", return_value=client) as client_factory,
    ):
        client = telegram_http_client()
    transport_factory.assert_called_once_with(local_address="::")
    client_factory.assert_called_once_with(transport=transport, timeout=10.0)
    assert client is client_factory.return_value


def test_telegram_proxy_client_does_not_bind_ipv6():
    client = Mock()
    with (
        patch.dict(
            os.environ,
            {
                "TELEGRAM_FORCE_IPV6": "true",
                "HTTPS_PROXY": "http://proxy.example:8000",
            },
            clear=True,
        ),
        patch("app.integrations.telegram.httpx.HTTPTransport") as transport_factory,
        patch("app.integrations.telegram.httpx.Client", return_value=client) as client_factory,
    ):
        client = telegram_http_client()
    transport_factory.assert_not_called()
    client_factory.assert_called_once_with(transport=None, timeout=10.0)
    assert client is client_factory.return_value
