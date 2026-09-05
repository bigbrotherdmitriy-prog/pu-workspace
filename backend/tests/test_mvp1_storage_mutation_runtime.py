from dataclasses import replace
from sqlalchemy.orm import Session

import pytest

from app.core.integration_types import StorageObject
from app.organizer_engine.storage_mutation_runtime import _state, SyntheticStorageMutationRuntime
from app.organizer_engine.storage_mutations import MutationCommand, MutationConflict, StorageBindingPin, StorageMutation


class SyntheticAdapter:
    synthetic_storage_adapter = True
    provider = "google_drive"

    def __init__(self):
        self.item = StorageObject("nested/file", "old.pdf", "application/pdf", "root/nested")
        self.calls = 0

    def get_file_meta(self, _object_id): return self.item
    def assert_inside_copy(self, object_id, root):
        if not object_id.startswith(root.rstrip("/") + "/"): raise AssertionError("outside")

    def object_revision(self, _object_id): return "sha-1"
    def rename_file(self, _object_id, name, _root):
        self.calls += 1
        self.item.name = name
    def move_file(self, _object_id, parent, _old_parent, _root):
        self.calls += 1
        self.item.parent_id = parent


def command():
    return MutationCommand("mutation:runtime:01", StorageBindingPin(1, "google_drive", "c1", "root", 1), 1,
                           (StorageMutation("rename", "root/nested/file", "rev1", "root/nested", "old.pdf",
                                            "root/nested", "standard.pdf"),))


def test_reconciliation_classifies_exact_before_after_and_unknown_states():
    adapter = SyntheticAdapter()
    assert _state(adapter, command()) == "before"
    adapter.item.name = "standard.pdf"
    assert _state(adapter, command()) == "after"
    adapter.item.name = "unexpected.pdf"
    assert _state(adapter, command()) == "unknown"


def test_runtime_is_default_deny_and_rejects_live_adapter_before_session_use():
    runtime = SyntheticStorageMutationRuntime(lambda: None, lambda _pin: None)
    with pytest.raises(MutationConflict, match="disabled"):
        runtime.execute(project_id=1)
    live = SyntheticAdapter()
    delattr(SyntheticAdapter, "synthetic_storage_adapter")
    try:
        with pytest.raises(MutationConflict, match="live_storage"):
            SyntheticStorageMutationRuntime._require_synthetic(live)
    finally:
        SyntheticAdapter.synthetic_storage_adapter = True


def test_reconciliation_never_treats_partial_state_as_applied():
    adapter = SyntheticAdapter()
    cmd = replace(command(), operations=command().operations + (
        StorageMutation("move", "root/nested/file", "rev1", "root/nested", "old.pdf", "root/other", "old.pdf"),
    ))
    adapter.item.name = "standard.pdf"
    assert _state(adapter, cmd) == "unknown"


def test_job_payload_contract_remains_ids_only():
    from app.organizer_engine.storage_mutation_jobs import validate_job_payload
    safe = {"project_id": 1, "proposal_id": 2, "action_id": 3, "command_key": "mutation:01",
            "expected_record_version": 1, "operation": "apply"}
    assert validate_job_payload(safe) == safe
    with pytest.raises(ValueError, match="unsafe"):
        validate_job_payload({**safe, "file_content": "forbidden"})


def test_db_runtime_persists_attempt_receipt_and_replays_without_second_effect(db_session):
    from test_mvp1_storage_mutation_repository import world

    project, _connection, _snapshot, action = world(db_session)
    db_session.commit()
    engine = db_session.get_bind()
    adapter = SyntheticAdapter()
    adapter.item.id = "root/nested/file"
    adapter.item.parent_id = "root/nested"

    runtime = SyntheticStorageMutationRuntime(lambda: Session(engine), lambda _pin: adapter, enabled=True)
    payload = {"project_id": project.id, "proposal_id": action.proposal_id, "action_id": action.id,
               "command_key": "mutation:db-runtime:01", "expected_record_version": 1,
               "operation": "apply"}
    first = runtime.execute(**payload)
    replay = runtime.execute(**payload)
    assert first == replay
    assert first["outcome"] == "applied"
    assert adapter.calls == 1
