"""The status API must not invent the worker's originals-preserved evidence."""
import pytest

from app.models.job import BackgroundJob
from test_storage_binding_validation import bound  # noqa: F401


@pytest.mark.parametrize("proof", [None, True, "false", 0, False])
def test_cleanup_status_preserves_only_explicit_boolean_worker_proof(bound, proof):
    result = {"trashed": 1}
    if proof is not None:
        result["originals_affected"] = proof
    with bound.db() as db:
        job = BackgroundJob(kind="workspace.safe_copy_cleanup", status="completed",
                            payload={"project_id": bound.new}, result=result,
                            idempotency_key="synthetic-cleanup-status-proof")
        db.add(job)
        db.commit()
        job_id = job.id
    response = bound.client.get(f"/projects/{bound.new}/safe-copies/cleanup/{job_id}")
    assert response.status_code == 200
    expected = proof if type(proof) is bool else None
    assert response.json()["originals_affected"] is expected
