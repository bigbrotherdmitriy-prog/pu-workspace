"""IDs-only durable-job seam for exact storage mutations."""
from __future__ import annotations

from typing import Protocol


ALLOWED_KEYS = {
    "project_id", "proposal_id", "action_id", "command_key",
    "expected_record_version", "operation",
}


class StorageMutationRuntime(Protocol):
    def execute(self, *, project_id: int, proposal_id: int, action_id: int,
                command_key: str, expected_record_version: int, operation: str) -> dict: ...


_runtime: StorageMutationRuntime | None = None


def install_runtime(runtime: StorageMutationRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def validate_job_payload(payload: dict) -> dict:
    if set(payload) != ALLOWED_KEYS:
        raise ValueError("unsafe_storage_mutation_payload")
    if payload["operation"] not in {"apply", "rollback"}:
        raise ValueError("unsafe_storage_mutation_operation")
    if not all(isinstance(payload[key], int) and payload[key] > 0
               for key in ("project_id", "proposal_id", "action_id", "expected_record_version")):
        raise ValueError("invalid_storage_mutation_reference")
    if not isinstance(payload["command_key"], str) or not 8 <= len(payload["command_key"]) <= 120:
        raise ValueError("invalid_storage_mutation_key")
    return payload


def run_storage_mutation_job(payload: dict) -> dict:
    safe = validate_job_payload(payload)
    if _runtime is None:
        raise RuntimeError("storage_mutation_runtime_unavailable")
    result = _runtime.execute(**safe)
    allowed = {"receipt_id", "outcome", "resulting_record_version"}
    if not isinstance(result, dict) or not set(result).issubset(allowed):
        raise RuntimeError("unsafe_storage_mutation_result")
    if result.get("outcome") not in {"applied", "compensated", "partial_failure", "rolled_back", "unknown"}:
        raise RuntimeError("invalid_storage_mutation_outcome")
    return result
