import asyncio
import json

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.observability import observe_request, request_id_context, valid_request_id


def _request(request_id: str | None = None) -> Request:
    headers = [] if request_id is None else [(b"x-request-id", request_id.encode())]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/status",
            "raw_path": b"/api/status",
            "query_string": b"secret=not-logged",
            "headers": headers,
            "scheme": "https",
            "server": ("test", 443),
            "client": ("test", 123),
        }
    )


def test_valid_request_id_preserves_safe_value_and_replaces_unsafe_value():
    assert valid_request_id("client:trace-42") == "client:trace-42"
    generated = valid_request_id("unsafe id\nvalue")
    assert len(generated) == 32
    assert generated.isalnum()


def test_observe_request_returns_correlation_header_and_scopes_context():
    seen = []

    async def next_handler(_request):
        seen.append(request_id_context.get())
        return JSONResponse({"ok": True})

    response = asyncio.run(observe_request(_request("demo-42"), next_handler))

    assert response.headers["x-request-id"] == "demo-42"
    assert seen == ["demo-42"]
    assert request_id_context.get() == ""


def test_observe_request_returns_safe_support_code_for_unhandled_error():
    async def failing_handler(_request):
        raise RuntimeError("private backend detail")

    response = asyncio.run(observe_request(_request("failure-42"), failing_handler))
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "failure-42"
    assert payload == {"detail": "Внутренняя ошибка. Код: failure-42"}
    assert "private backend detail" not in response.body.decode()
    assert request_id_context.get() == ""
