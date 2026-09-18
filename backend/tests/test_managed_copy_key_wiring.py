"""Regression guard for the gap found in the storage-adapters preflight (2.4.d):
DriveClient.copy_folder_tree's idempotency_key correctly reconciles a retried
copy (see test_drive_safety.py), but that primitive is only as good as its
one real caller actually passing a stable key. This proves organizer.py's
_scan_worker does — without standing up the rest of its pipeline (index,
proposal, tasks, drafts, governance, Telegram), which is unrelated to this
one wiring fact.
"""

from types import SimpleNamespace

import pytest


class _StopAtCopy(Exception):
    """Raised by the fake drive client right after recording the call it
    received, so the rest of _scan_worker's pipeline never has to be mocked.
    """


class _FakeDrive:
    provider = "google_drive"

    def __init__(self, calls):
        self._calls = calls

    def get_object(self, folder_id):
        return SimpleNamespace(is_folder=True, name="Source", parent_id="parent-id")

    def walk_tree(self, folder_id):
        return []

    def copy_folder_tree(self, *args, **kwargs):
        self._calls.append(kwargs)
        raise _StopAtCopy()


def test_scan_worker_passes_a_session_stable_idempotency_key_to_copy_folder_tree(monkeypatch):
    from app import organizer

    calls: list[dict] = []
    session = {"copy_folder_id": None, "retry_count": 0, "progress": 0}
    repo = SimpleNamespace(
        get_session=lambda _session_id: session,
        proposal_for_session=lambda _session_id: None,
        project=lambda _project_id: {"id": 4, "name": "Synthetic", "archived_at": None},
        update_session=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(organizer, "SessionLocal", lambda: SimpleNamespace(close=lambda: None, rollback=lambda: None))
    monkeypatch.setattr(organizer, "OrganizerRepository", lambda db: repo)
    monkeypatch.setattr(organizer, "storage_for_project", lambda *args, **kwargs: _FakeDrive(calls))
    monkeypatch.setattr(organizer, "notify_telegram", lambda *args, **kwargs: None)

    with pytest.raises(_StopAtCopy):
        organizer._scan_worker(482, 4, "source-folder-id", raise_errors=True)

    assert len(calls) == 1
    assert calls[0].get("idempotency_key") == "organizer-session-482"


def test_scan_worker_idempotency_key_is_stable_across_a_crash_recovery_retry(monkeypatch):
    """The whole point of the key: it must be identical on a retry of the
    same session, so copy_folder_tree can recognise its own prior attempt.
    """
    from app import organizer

    calls: list[dict] = []
    session = {"copy_folder_id": None, "retry_count": 1, "progress": 0}
    repo = SimpleNamespace(
        get_session=lambda _session_id: session,
        proposal_for_session=lambda _session_id: None,
        project=lambda _project_id: {"id": 4, "name": "Synthetic", "archived_at": None},
        update_session=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(organizer, "SessionLocal", lambda: SimpleNamespace(close=lambda: None, rollback=lambda: None))
    monkeypatch.setattr(organizer, "OrganizerRepository", lambda db: repo)
    monkeypatch.setattr(organizer, "storage_for_project", lambda *args, **kwargs: _FakeDrive(calls))
    monkeypatch.setattr(organizer, "notify_telegram", lambda *args, **kwargs: None)

    for _ in range(2):  # simulate the original attempt, then a recovery retry
        with pytest.raises(_StopAtCopy):
            organizer._scan_worker(482, 4, "source-folder-id", raise_errors=True)

    assert len(calls) == 2
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"] == "organizer-session-482"
