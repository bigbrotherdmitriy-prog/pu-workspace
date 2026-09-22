import hashlib

from sqlalchemy import func, select

from app.api.contract_discovery import _contract_discovery_batches
from app.models.job import BackgroundJob
from app.models.organization_contract import Contract


def test_postgres_contract_discovery_638_documents_are_durable_batched_and_create_no_contracts(
    pg_session, project_factory,
):
    _, project = project_factory()
    pins = [{"document_id": value, "version_id": value + 10_000} for value in range(1, 639)]
    batches = _contract_discovery_batches(pins)
    before = pg_session.scalar(select(func.count()).select_from(Contract)) or 0

    for batch in batches:
        fingerprint = hashlib.sha256(
            ",".join(f"{item['document_id']}:{item['version_id']}" for item in batch).encode()
        ).hexdigest()
        pg_session.add(BackgroundJob(
            kind="contracts.discover_batch",
            payload={"project_id": project.id, "document_versions": batch},
            status="queued",
            idempotency_key=f"contract-discovery-test:{project.id}:{fingerprint}",
        ))
    pg_session.flush()

    stored = list(pg_session.scalars(select(BackgroundJob).where(
        BackgroundJob.kind == "contracts.discover_batch",
        BackgroundJob.payload["project_id"].as_integer() == project.id,
    ).order_by(BackgroundJob.id)))
    assert [len(job.payload["document_versions"]) for job in stored] == [200, 200, 200, 38]
    assert all(job.status == "queued" and job.idempotency_key for job in stored)
    assert (pg_session.scalar(select(func.count()).select_from(Contract)) or 0) == before
