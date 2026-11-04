"""Bound accumulated request bytes; ASGI/proxy frame limits remain separate."""
from fastapi import HTTPException, Request


async def read_body_limited(request: Request, max_bytes: int, detail: str) -> bytes:
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("Body limit must be a positive integer")
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit():
        digits = declared.lstrip("0") or "0"
        maximum = str(max_bytes)
        if len(digits) > len(maximum) or (len(digits) == len(maximum) and digits > maximum):
            raise HTTPException(413, detail)
    data = bytearray()
    async for chunk in request.stream():
        if len(chunk) > max_bytes - len(data):
            raise HTTPException(413, detail)
        data.extend(chunk)
    return bytes(data)
