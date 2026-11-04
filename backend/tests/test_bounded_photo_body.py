"""Codex fallback: synthetic async stream and static wiring, never an API route."""
import ast
import asyncio
from pathlib import Path
import pytest
from fastapi import HTTPException
from app.bounded_body import read_body_limited


class SyntheticBody:
    def __init__(self, chunks, declared=None):
        self.chunks = chunks
        self.headers = {} if declared is None else {'content-length': declared}
        self.reads = 0

    async def stream(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def body(self):
        raise AssertionError('Unbounded body() must never be called')


@pytest.mark.parametrize('declared', [None, 'bogus', '1', '-1'])
def test_actual_bytes_are_bounded_even_without_trusted_length(declared):
    request = SyntheticBody([b'abcd', b'efghi', b'never-read'], declared)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_body_limited(request, 8, 'Synthetic photo too large'))
    assert exc.value.status_code == 413
    assert request.reads == 2


@pytest.mark.parametrize('declared', ['9', '0009', '9' * 5000])
def test_declared_oversize_rejected_without_reading_stream(declared):
    request = SyntheticBody([b'never-read'], declared)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_body_limited(request, 8, 'Synthetic photo too large'))
    assert exc.value.status_code == 413 and request.reads == 0


def test_exact_boundary_accepts_bytes_and_ignores_empty_chunks():
    request = SyntheticBody([b'', b'abcd', b'efgh', b''], '0008')
    assert asyncio.run(read_body_limited(request, 8, 'Synthetic photo too large')) == b'abcdefgh'


def test_photo_route_uses_bound_after_role_check_before_service_write():
    tree = ast.parse((Path(__file__).parents[1] / 'app/api/defects.py').read_text())
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'upload_photo')
    calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)]
    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr == 'body' for node in calls)
    bound = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'read_body_limited')
    auth = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'require_project_role')
    write = next(node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == 'add_photo')
    assert ast.unparse(bound.args[1]) == 'svc.MAX_PHOTO_BYTES'
    assert auth.lineno < bound.lineno < write.lineno
