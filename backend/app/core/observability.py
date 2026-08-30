import contextvars
import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse


log = logging.getLogger("uvicorn.error")
log.setLevel(logging.INFO)
request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def valid_request_id(value: str | None) -> str:
    """Keep a safe caller id or create one without logging private request data."""
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid4().hex


async def observe_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = valid_request_id(request.headers.get("x-request-id"))
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        log.exception(
            "request_failed request_id=%s method=%s path=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000,
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": f"Внутренняя ошибка. Код: {request_id}"},
            headers={"X-Request-ID": request_id},
        )
        return response
    finally:
        if status_code != 500:
            log.info(
                "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                request_id,
                request.method,
                request.url.path,
                status_code,
                (time.perf_counter() - started) * 1000,
            )
        request_id_context.reset(token)
