import base64
import inspect
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter

from app.api import local_upload
from app.api.local_upload import LocalBatch, LocalFile, analyze_local_folder, decode_local_file


def test_local_file_base64_is_decoded():
    item = LocalFile(path="Проект/письмо.txt", mime_type="text/plain", content_base64=base64.b64encode("текст".encode()).decode())
    assert decode_local_file(item) == "текст".encode()


def test_invalid_local_file_is_rejected():
    with pytest.raises(ValueError):
        decode_local_file(LocalFile(path="bad.txt", content_base64="%%%"))


def _synthetic_batch():
    return LocalBatch(
        project_id=7,
        files=[
            LocalFile(
                path="synthetic/note.txt",
                mime_type="text/plain",
                content_base64=base64.b64encode(b"synthetic").decode(),
            )
        ],
    )


def _synthetic_user():
    return SimpleNamespace(id=2)


def _forbidden(name):
    def _raise(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")

    return _raise


def _install_forbidden_downstream(monkeypatch):
    monkeypatch.setattr(local_upload, "get_local_upload_runtime", _forbidden("get_local_upload_runtime"))
    monkeypatch.setattr(local_upload, "decode_local_file", _forbidden("decode_local_file"))
    monkeypatch.setattr(local_upload, "admit_candidate", _forbidden("admit_candidate"))
    monkeypatch.setattr(local_upload, "stage_and_enqueue", _forbidden("stage_and_enqueue"))


def test_expected_user_mismatch_raises_403_before_any_downstream(monkeypatch):
    _install_forbidden_downstream(monkeypatch)
    monkeypatch.setattr(local_upload, "require_project_role", _forbidden("require_project_role"))

    with pytest.raises(HTTPException) as excinfo:
        analyze_local_folder(
            payload=_synthetic_batch(),
            db=object(),
            user=_synthetic_user(),
            idempotency_key=None,
            expected_user_id=1,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "offline_actor_mismatch"


def test_matching_expected_user_reaches_manager_role_check(monkeypatch):
    _install_forbidden_downstream(monkeypatch)
    calls = []

    def _role_stop(db, user, project_id, role):
        calls.append((db, user, project_id, role))
        raise HTTPException(403, "role_stop_sentinel")

    monkeypatch.setattr(local_upload, "require_project_role", _role_stop)

    with pytest.raises(HTTPException) as excinfo:
        analyze_local_folder(
            payload=_synthetic_batch(),
            db=object(),
            user=_synthetic_user(),
            idempotency_key=None,
            expected_user_id=2,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "role_stop_sentinel"
    assert len(calls) == 1
    assert calls[0][2] == 7
    assert calls[0][3] == "manager"


def test_omitted_expected_user_reaches_manager_role_check(monkeypatch):
    _install_forbidden_downstream(monkeypatch)
    calls = []

    def _role_stop(db, user, project_id, role):
        calls.append((db, user, project_id, role))
        raise HTTPException(403, "role_stop_sentinel")

    monkeypatch.setattr(local_upload, "require_project_role", _role_stop)

    with pytest.raises(HTTPException) as excinfo:
        analyze_local_folder(
            payload=_synthetic_batch(),
            db=object(),
            user=_synthetic_user(),
            idempotency_key=None,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "role_stop_sentinel"
    assert len(calls) == 1
    assert calls[0][2] == 7
    assert calls[0][3] == "manager"


def test_expected_user_default_is_plain_none():
    default = inspect.signature(analyze_local_folder).parameters["expected_user_id"].default
    assert default is None


def _expected_user_adapter():
    hints = get_type_hints(analyze_local_folder, include_extras=True)
    return TypeAdapter(hints["expected_user_id"])


@pytest.mark.parametrize("raw", ["0", "-1", "-42"])
@pytest.mark.parametrize("as_string", [False, True])
def test_expected_user_annotation_rejects_non_positive(raw, as_string):
    adapter = _expected_user_adapter()
    value = raw if as_string else int(raw)
    with pytest.raises(Exception):
        adapter.validate_python(value)


@pytest.mark.parametrize("raw", ["abc", "1.5", ""])
def test_expected_user_annotation_rejects_non_numeric_strings(raw):
    adapter = _expected_user_adapter()
    with pytest.raises(Exception):
        adapter.validate_python(raw)


def test_expected_user_annotation_parses_positive_string():
    adapter = _expected_user_adapter()
    assert adapter.validate_python("2") == 2
