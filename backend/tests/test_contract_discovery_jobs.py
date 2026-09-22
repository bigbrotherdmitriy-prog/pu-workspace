import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import app.api.contract_discovery as discovery
from app.api.contract_discovery import ContractDiscoveryJobsRequest, start_contract_discovery_jobs
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.job import BackgroundJob
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


def _project(db_session, name: str) -> Project:
    organization = Organization(name=f"Организация {name}")
    db_session.add(organization); db_session.flush()
    project = Project(name=name, organization_id=organization.id)
    db_session.add(project); db_session.flush()
    return project


def _documents(db_session, project_id: int, count: int) -> list[Document]:
    rows = []
    for index in range(count):
        document = Document(project_id=project_id, name=f"Документ {index}.pdf", source="google_drive")
        db_session.add(document); db_session.flush()
        db_session.add(DocumentVersion(document_id=document.id, version_number=1, content="Письмо по договору № Д-1"))
        rows.append(document)
    db_session.commit()
    return rows


def test_start_jobs_chunks_201_documents_and_is_idempotent(db_session, user_factory, monkeypatch):
    project = _project(db_session, "Проект")
    rows = _documents(db_session, project.id, 201)
    user = user_factory(); db_session.commit()
    monkeypatch.setattr(discovery, "require_project_role", lambda *_args, **_kwargs: None)

    first = start_contract_discovery_jobs(project.id, ContractDiscoveryJobsRequest(), db_session, user)
    second = start_contract_discovery_jobs(project.id, ContractDiscoveryJobsRequest(), db_session, user)

    assert [job["count"] for job in first["jobs"]] == [200, 1]
    assert [job["job_id"] for job in second["jobs"]] == [job["job_id"] for job in first["jobs"]]
    stored = list(db_session.scalars(select(BackgroundJob).where(BackgroundJob.kind == "contracts.discover_batch")))
    assert len(stored) == 2
    assert all(len(job.payload["document_versions"]) <= 200 for job in stored)
    assert all(job.payload.get("job_id") == job.id for job in stored)
    assert (db_session.scalar(select(func.count()).select_from(Contract)) or 0) == 0

    stored[0].status = "dead_letter"; stored[0].attempts = 3; db_session.commit()
    restarted = start_contract_discovery_jobs(project.id, ContractDiscoveryJobsRequest(), db_session, user)
    db_session.refresh(stored[0])
    assert restarted["jobs"][0]["job_id"] == stored[0].id
    assert stored[0].status == "queued" and stored[0].attempts == 0


def test_start_jobs_rejects_document_from_another_project(db_session, user_factory, monkeypatch):
    own = _project(db_session, "Свой")
    foreign = _project(db_session, "Чужой")
    foreign_document = _documents(db_session, foreign.id, 1)[0]
    user = user_factory(); db_session.commit()
    monkeypatch.setattr(discovery, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        start_contract_discovery_jobs(
            own.id, ContractDiscoveryJobsRequest(document_ids=[foreign_document.id]), db_session, user,
        )
    assert error.value.status_code == 404
