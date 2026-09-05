from types import SimpleNamespace

import pytest

from app.organizer_engine.managed_copies import managed_copy_identity, snapshot_copy_key


def test_snapshot_retry_uses_the_same_opaque_copy_identity():
    source = SimpleNamespace(external_id="synthetic-folder")
    snapshot = SimpleNamespace(id=12, project_id=4, analysis_result={"storage_binding": {
        "project_id": 4, "folder_id": source.external_id, "provider": "google_drive",
        "connection_id": "synthetic-account", "connection_row_id": 8,
    }})
    first = snapshot_copy_key(snapshot, source)
    assert snapshot_copy_key(snapshot, source) == first
    assert first.startswith("managed-") and len(first) == 40
    assert "synthetic" not in first
    snapshot.id = 13
    assert snapshot_copy_key(snapshot, source) != first


@pytest.mark.parametrize("field,value", [
    ("project_id", 9), ("provider", "yandex_disk"), ("connection_id", "other"),
    ("connection_row_id", 5), ("folder_id", "other"), ("source_revision", "2"),
])
def test_each_exact_binding_dimension_changes_identity(field, value):
    binding = dict(project_id=4, provider="google_drive", connection_id="synthetic-account",
                   connection_row_id=8, folder_id="synthetic-folder", source_revision="1")
    assert managed_copy_identity(**binding) != managed_copy_identity(**{**binding, field: value})


def test_missing_snapshot_binding_fails_closed():
    with pytest.raises(ValueError, match="managed_copy_binding_unavailable"):
        snapshot_copy_key(SimpleNamespace(id=12, project_id=4, analysis_result={}),
                          SimpleNamespace(external_id="synthetic-folder"))


def test_copy_worker_does_not_activate_unproven_name_reuse(monkeypatch):
    from app import organizer
    calls = []
    session = {"copy_folder_id": None, "retry_count": 0}
    repo = SimpleNamespace(
        get_session=lambda _: session,
        proposal_for_session=lambda _: None,
        project=lambda _: {"id": 4, "name": "Synthetic", "archived_at": None},
        update_session=lambda *args, **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(organizer, "SessionLocal", lambda: SimpleNamespace(close=lambda: None, rollback=lambda: None))
    monkeypatch.setattr(organizer, "OrganizerRepository", lambda db: repo)
    # No read/copy methods: none may be invoked before capability confirmation.
    monkeypatch.setattr(organizer, "storage_for_project", lambda *args: SimpleNamespace())
    monkeypatch.setattr(organizer, "notify_telegram", lambda *args: None)
    with pytest.raises(ValueError, match="managed_copy_reconciliation_unavailable"):
        organizer._scan_worker(1, 4, "synthetic-folder", managed_copy_key="managed-synthetic", raise_errors=True)
    assert calls[-1]["status"] == "failed"
