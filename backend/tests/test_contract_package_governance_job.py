"""§11: analyze_contract_package keeps regex-based financial checks synchronous
and defers create_governance_items (LLM) to a background job."""
from contextlib import nullcontext
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import contract_package
from app.api.contract_package import (
    _analyze_governance_job,
    analyze_contract_package,
    get_contract_package_analysis_job,
)
from app.models.document import Document
from app.models.governance import Risk
from app.models.job import BackgroundJob
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


@pytest.fixture
def world(db_session, user_factory, monkeypatch):
    monkeypatch.setattr(contract_package, "SessionLocal", lambda: nullcontext(db_session))
    user = user_factory()
    org = Organization(name="Synthetic Org")
    db_session.add(org)
    db_session.flush()
    project = Project(name="Synthetic Project", organization_id=org.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    contract = Contract(project_id=project.id, number="C-1", title="Synthetic contract", amount=Decimal("100"))
    db_session.add(contract)
    db_session.flush()
    db_session.commit()
    return db_session, user, project, contract


def _document(db, project_id, summary, *, name="doc.docx"):
    row = Document(project_id=project_id, name=name, summary=summary, source="local_upload")
    db.add(row); db.flush()
    return row


def test_financials_stay_synchronous_governance_is_deferred(world, monkeypatch):
    db, user, project, contract = world
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    document = _document(db, project.id, "Цена договора 120 руб. Есть риск срыва поставки.")
    db.commit()
    contract.source_document_id = document.id
    db.commit()

    result = analyze_contract_package(project.id, contract.id, db, user)

    # Sync part: financial mismatch found immediately, no LLM involved.
    assert result["issue_count"] == 1
    assert result["issues"][0]["field"] == "amount"
    # Async part: nothing materialized yet.
    assert result["risks"] is None and result["decisions"] is None
    assert result["governance_already_running"] is False
    assert result["governance_status"] == "queued"
    assert db.scalar(select(Risk).where(Risk.project_id == project.id)) is None

    job = db.get(BackgroundJob, result["governance_job_id"])
    assert job.kind == "contract_package.analyze_governance"
    assert job.payload["document_ids"] == [document.id]
    job.status = "running"; db.commit()

    outcome = _analyze_governance_job(job.payload)
    assert outcome["risks"] == 1
    risk = db.scalar(select(Risk).where(Risk.project_id == project.id))
    assert risk is not None and "риск срыва поставки" in risk.source_excerpt


def test_no_documents_with_content_skips_job_entirely(world):
    db, user, project, contract = world
    result = analyze_contract_package(project.id, contract.id, db, user)
    assert result["governance_job_id"] is None
    assert result["governance_status"] is None
    assert db.scalar(select(BackgroundJob).where(BackgroundJob.kind == "contract_package.analyze_governance")) is None


def test_guards_against_duplicate_governance_job_per_contract(world):
    db, user, project, contract = world
    document = _document(db, project.id, "Обычный текст без рисков и сумм.")
    db.commit()
    contract.source_document_id = document.id
    db.commit()

    first = analyze_contract_package(project.id, contract.id, db, user)
    assert first["governance_already_running"] is False
    second = analyze_contract_package(project.id, contract.id, db, user)
    assert second["governance_already_running"] is True
    assert second["governance_job_id"] == first["governance_job_id"]
    assert len(list(db.scalars(select(BackgroundJob).where(
        BackgroundJob.kind == "contract_package.analyze_governance",
    )))) == 1


def test_get_governance_job_scoped_to_contract_and_normalizes_status(world):
    db, user, project, contract = world
    document = _document(db, project.id, "Есть риск срыва поставки.")
    db.commit()
    contract.source_document_id = document.id
    db.commit()
    result = analyze_contract_package(project.id, contract.id, db, user)
    job_id = result["governance_job_id"]

    status = get_contract_package_analysis_job(project.id, contract.id, job_id, db, user)
    assert status["status"] == "queued"

    job = db.get(BackgroundJob, job_id)
    job.status = "completed"; db.commit()
    status_after = get_contract_package_analysis_job(project.id, contract.id, job_id, db, user)
    assert status_after["status"] == "succeeded"

    other_contract = Contract(project_id=project.id, number="C-2", title="Other contract")
    db.add(other_contract); db.commit()
    with pytest.raises(HTTPException) as wrong_contract:
        get_contract_package_analysis_job(project.id, other_contract.id, job_id, db, user)
    assert wrong_contract.value.status_code == 404
