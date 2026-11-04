"""Codex fallback: synthetic duplicate proposals must remain server-side read-only."""
from datetime import date
from decimal import Decimal
from hashlib import sha256

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api import execution_finance as api
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import CashFlowEntry, InvoiceExtractionProposal
from app.models.organization_contract import Organization
from app.models.project import Project


def make_duplicate_world(db, actor):
    org = Organization(name="SYNTHETIC duplicate test")
    db.add(org); db.flush()
    project = Project(name="SYNTHETIC", organization_id=org.id)
    db.add(project); db.flush()
    rows = []
    for index, (number, status) in enumerate([(123, "confirmed"), (123, "proposed"), (456, "proposed")]):
        content = f"SYNTHETIC distinct document {index}"
        document = Document(project_id=project.id, name=f"SYNTHETIC-{index}.pdf", current_version=1)
        db.add(document); db.flush()
        version = DocumentVersion(document_id=document.id, version_number=1, content=content)
        db.add(version); db.flush()
        row = InvoiceExtractionProposal(
            project_id=project.id, source_document_id=document.id,
            source_document_version_id=version.id, source_document_sha256=sha256(content.encode()).hexdigest(),
            amount=Decimal("100"), counterparty="SYNTHETIC supplier",
            payment_purpose=f"SYNTHETIC счёт № {number}", planned_date=date(2026, 10, 5),
            status=status, target_kind="cash_flow", extraction_method="llm", confidence=0.9,
        )
        db.add(row); db.flush(); rows.append(row)
    db.commit()
    return db, actor, rows


@pytest.fixture
def duplicate_world(db_session, user_factory):
    return make_duplicate_world(db_session, user_factory(is_admin=True))


def snapshot(db, row):
    db.refresh(row)
    return (row.amount, row.counterparty, row.payment_purpose, row.planned_date,
            row.selected_cost_category_id, row.status,
            db.scalar(select(func.count()).select_from(AuditLog)),
            db.scalar(select(func.count()).select_from(CashFlowEntry)))


@pytest.mark.parametrize("patch", [
    {"amount": "101"}, {"counterparty": "SYNTHETIC another supplier"},
    {"payment_purpose": "SYNTHETIC счёт № 999"}, {"planned_date": "2026-10-06"},
    {"amount": "100"}, {},
])
def test_detected_duplicate_cannot_be_edited_or_disguised(duplicate_world, patch):
    db, actor, rows = duplicate_world
    repeated = rows[1]
    assert api._proposal_view(db, repeated)["duplicate_of"] == "SYNTHETIC-0.pdf"
    before = snapshot(db, repeated)
    with pytest.raises(HTTPException) as failure:
        api.update_invoice_extraction(repeated.id, api.InvoiceExtractionUpdate(**patch), db, actor)
    assert failure.value.status_code == 409
    assert "Повторный счёт" in failure.value.detail
    assert snapshot(db, repeated) == before
    assert api._proposal_view(db, repeated)["duplicate_of"] == "SYNTHETIC-0.pdf"


def test_nonduplicate_remains_editable(duplicate_world):
    db, actor, rows = duplicate_world
    other = rows[2]
    assert api._proposal_view(db, other)["duplicate_of"] is None
    result = api.update_invoice_extraction(other.id, api.InvoiceExtractionUpdate(amount="101"), db, actor)
    db.expire_all()
    assert db.get(InvoiceExtractionProposal, other.id).amount == Decimal("101.00")
    assert result["duplicate_of"] is None


def test_foreign_actor_is_denied_before_duplicate_information(duplicate_world, user_factory, monkeypatch):
    db, _actor, rows = duplicate_world
    foreign = user_factory(is_admin=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Duplicate lookup happened before authorization")
    monkeypatch.setattr(api, "_find_repeated_invoice", forbidden)
    with pytest.raises(HTTPException) as failure:
        api.update_invoice_extraction(rows[1].id, api.InvoiceExtractionUpdate(amount="101"), db, foreign)
    assert failure.value.status_code == 403


def test_proposed_duplicate_cannot_create_second_financial_fact(duplicate_world):
    db, actor, rows = duplicate_world
    before = db.scalar(select(func.count()).select_from(CashFlowEntry))
    with pytest.raises(HTTPException) as failure:
        api.confirm_invoice_extraction(rows[1].id, api.InvoiceExtractionConfirm(), db, actor)
    assert failure.value.status_code == 409
    assert db.scalar(select(func.count()).select_from(CashFlowEntry)) == before
    assert rows[1].status == "proposed"


def test_two_distinct_files_same_requisites_keep_one_fact(db_session, user_factory, monkeypatch):
    from app.invoice_extraction import InvoiceFields
    from tests.test_invoice_confirm_as_fact import _proposal, _plan_line
    from tests.test_invoice_cost_classification import _world
    db = db_session
    actor, project, document, _version = _world(db, user_factory)
    original_id = _proposal(db, actor, project, document, monkeypatch)
    _contract, _category, plan = _plan_line(db, project)
    confirmed = api.confirm_invoice_extraction(original_id, api.InvoiceExtractionConfirm(plan_entry_id=plan.id), db, actor)
    original_fact = confirmed["created_cash_flow_id"]
    assert db.scalars(select(CashFlowEntry.id).where(CashFlowEntry.plan_entry_id == plan.id)).all() == [original_fact]
    original = db.get(InvoiceExtractionProposal, original_id)
    copy_document = Document(project_id=project.id, name="SYNTHETIC-different-copy.pdf", current_version=1, source="local_upload", status="analyzed")
    db.add(copy_document); db.flush()
    copy_content = f"SYNTHETIC different file. Supplier {original.counterparty}; {original.payment_purpose}; total {original.amount} {original.currency}"
    db.add(DocumentVersion(document_id=copy_document.id, version_number=1, content=copy_content))
    db.flush()
    monkeypatch.setattr(api, "extract_invoice_fields", lambda *_args, **_kw: InvoiceFields(
        amount=original.amount, amount_evidence_quote=str(original.amount),
        currency=original.currency, counterparty=original.counterparty,
        counterparty_evidence_quote=original.counterparty,
        payment_purpose=original.payment_purpose, payment_purpose_evidence_quote=original.payment_purpose,
        planned_date=original.planned_date, suggested_category_name=None, category_evidence_quote=None,
        confidence=.9, extraction_method="llm",
    ))
    repeated = api.create_invoice_extraction(copy_document.id, api.InvoiceExtractionCreate(project_id=project.id), db, actor)
    assert repeated["id"] != original_id and repeated["duplicate_of"] == document.name
    with pytest.raises(HTTPException) as edit:
        api.update_invoice_extraction(repeated["id"], api.InvoiceExtractionUpdate(amount="999"), db, actor)
    assert edit.value.status_code == 409
    with pytest.raises(HTTPException) as confirm:
        api.confirm_invoice_extraction(repeated["id"], api.InvoiceExtractionConfirm(plan_entry_id=plan.id), db, actor)
    assert confirm.value.status_code == 409
    replay = api.confirm_invoice_extraction(original_id, api.InvoiceExtractionConfirm(plan_entry_id=plan.id), db, actor)
    assert replay["created_cash_flow_id"] == original_fact
    assert db.scalars(select(CashFlowEntry.id).where(CashFlowEntry.plan_entry_id == plan.id)).all() == [original_fact]


@pytest.fixture
def http_world():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.database import Base, get_db
    from app.core.auth import require_user
    from app.models.user import User
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            actor = User(name="SYNTHETIC manager", email="synthetic@example.test", is_admin=True)
            foreign = User(name="SYNTHETIC foreign", email="foreign@example.test", is_admin=False)
            db.add_all([actor, foreign]); db.flush()
            _db, actor, rows = make_duplicate_world(db, actor)
            current = {"actor": actor}
            app = FastAPI(); app.include_router(api.router)
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[require_user] = lambda: current["actor"]
            with TestClient(app) as client:
                yield client, db, current, rows, foreign
    finally:
        engine.dispose()


def test_api_duplicate_readonly_and_foreign_scope(http_world):
    client, db, current, rows, foreign = http_world
    path = f"/execution/invoice-extraction-proposals/{rows[1].id}"
    before = client.get(path)
    assert before.status_code == 200 and before.json()["duplicate_of"] == "SYNTHETIC-0.pdf"
    edit = client.patch(path, json={"amount": "101", "counterparty": "SYNTHETIC changed"})
    assert edit.status_code == 409 and "Повторный счёт" in edit.json()["detail"]
    assert client.get(path).json() == before.json()
    assert client.post(path + "/confirm", json={}).status_code == 409
    assert db.scalar(select(func.count()).select_from(CashFlowEntry)) == 0
    normal = client.patch(f"/execution/invoice-extraction-proposals/{rows[2].id}", json={"amount": "101"})
    assert normal.status_code == 200
    current["actor"] = foreign
    denial = client.patch(path, json={"amount": "102"})
    assert denial.status_code == 403 and "SYNTHETIC-0.pdf" not in denial.text
