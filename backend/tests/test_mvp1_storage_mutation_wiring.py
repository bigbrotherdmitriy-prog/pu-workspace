import pytest

from app.jobs.handlers import run
from app.organizer_engine.storage_mutation_jobs import install_runtime, validate_job_payload


def payload(operation="apply"):
    return {
        "project_id": 7,
        "proposal_id": 11,
        "action_id": 13,
        "command_key": "storage:mutation:0001",
        "expected_record_version": 4,
        "operation": operation,
    }


class Runtime:
    def __init__(self): self.calls = []
    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"receipt_id": 17, "outcome": "applied", "resulting_record_version": 5}


def test_durable_handler_passes_only_ids_to_installed_runtime():
    runtime = Runtime()
    install_runtime(runtime)
    try:
        result = run("workspace.storage_mutation", payload())
    finally:
        install_runtime(None)
    assert result == {"receipt_id": 17, "outcome": "applied", "resulting_record_version": 5}
    assert runtime.calls == [payload()]


@pytest.mark.parametrize("forbidden", ["content", "path", "token", "connection_id", "folder_id", "source_revision"])
def test_payload_rejects_inline_binding_content_and_secrets(forbidden):
    unsafe = {**payload(), forbidden: "must-not-cross-queue"}
    with pytest.raises(ValueError, match="unsafe_storage_mutation_payload"):
        validate_job_payload(unsafe)


def test_missing_runtime_and_unknown_outcome_fail_closed():
    install_runtime(None)
    with pytest.raises(RuntimeError, match="runtime_unavailable"):
        run("workspace.storage_mutation", payload())

    class UnknownRuntime(Runtime):
        def execute(self, **kwargs): return {"outcome": "success-ish"}

    install_runtime(UnknownRuntime())
    try:
        with pytest.raises(RuntimeError, match="invalid_storage_mutation_outcome"):
            run("workspace.storage_mutation", payload("rollback"))
    finally:
        install_runtime(None)
