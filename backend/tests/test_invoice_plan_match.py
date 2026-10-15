from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.execution_finance import (
    CashFlowEntry,
    InvoiceExtractionProposal,
)
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Plan match org")
        user = User(name="Viewer", email="plan-match-viewer@example.test", is_admin=False)
        outsider = User(name="Outsider", email="plan-match-outsider@example.test", is_admin=False)
        db.add_all([org, user, outsider])
        db.flush()

        project = Project(name="Plan match project", organization_id=org.id)
        other_project = Project(name="Other project", organization_id=org.id)
        db.add_all([project, other_project])
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="viewer"))

        db.commit()

        old_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_user] = lambda: user
        try:
            client = TestClient(app)
            yield {
                "db": db,
                "client": client,
                "project": project,
                "other_project": other_project,
                "user": user,
                "outsider": outsider,
            }
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
    engine.dispose()


def _add_entry(db, project_id, **kwargs):
    values = {
        "direction": "outflow",
        "title": "Op",
        "planned_date": date(2026, 1, 15),
        "actual_date": None,
        "planned_amount": Decimal("0.00"),
        "actual_amount": Decimal("0.00"),
        "currency": "RUB",
        "status": "proposed",
    }
    values.update(kwargs)
    entry = CashFlowEntry(project_id=project_id, **values)
    db.add(entry)
    db.flush()
    return entry


def _add_proposal(db, project_id, **kwargs):
    values = {
        "source_document_id": 1,
        "source_document_version_id": 1,
        "source_document_sha256": "0" * 64,
        "extraction_method": "manual",
        "target_kind": "cash_flow",
        "status": "proposed",
        "currency": "RUB",
        "confidence": 0.0,
    }
    values.update(kwargs)
    proposal = InvoiceExtractionProposal(project_id=project_id, **values)
    db.add(proposal)
    db.flush()
    return proposal


def test_matching_counterparty_is_recommended(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]

    matched_entry = _add_entry(
        db,
        project.id,
        title="Оплата материалов",
        counterparty="ООО Ромашка",
        category="Материалы",
        planned_amount=Decimal("1000.00"),
    )
    _add_entry(
        db,
        project.id,
        title="Логистика",
        counterparty="ООО Лютик",
        category="Логистика",
        planned_amount=Decimal("500.00"),
    )
    proposal = _add_proposal(
        db,
        project.id,
        amount=Decimal("300.00"),
        counterparty='ООО "Ромашка"',
        payment_purpose="Оплата материалов по счёту",
    )
    db.commit()

    response = client.get(
        f"/projects/{project.id}/invoice-extraction-proposals/{proposal.id}/plan-line-suggestions"
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["recommended"] == matched_entry.id
    assert payload["additional_recommended"] is False
    assert payload["suggestions"], payload
    assert payload["suggestions"][0]["plan_entry_id"] == matched_entry.id
    assert payload["suggestions"][0]["score"] >= 50


def test_unknown_counterparty_triggers_additional_recommended(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]

    _add_entry(
        db,
        project.id,
        title="Оплата материалов",
        counterparty="ООО Ромашка",
        category="Материалы",
        planned_amount=Decimal("1000.00"),
    )
    _add_entry(
        db,
        project.id,
        title="Логистика",
        counterparty="ООО Лютик",
        category="Логистика",
        planned_amount=Decimal("500.00"),
    )
    proposal = _add_proposal(
        db,
        project.id,
        amount=Decimal("100.00"),
        counterparty="ООО Неизвестный",
        payment_purpose="Оплата по счёту без соответствия",
    )
    db.commit()

    response = client.get(
        f"/projects/{project.id}/invoice-extraction-proposals/{proposal.id}/plan-line-suggestions"
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["additional_recommended"] is True
    assert payload["recommended"] is None


def test_proposal_from_other_project_is_not_visible(world):
    db = world["db"]
    client = world["client"]
    project = world["project"]
    other_project = world["other_project"]

    proposal = _add_proposal(
        db,
        other_project.id,
        amount=Decimal("100.00"),
        counterparty="ООО Ромашка",
        payment_purpose="Оплата",
    )
    db.commit()

    response = client.get(
        f"/projects/{project.id}/invoice-extraction-proposals/{proposal.id}/plan-line-suggestions"
    )
    assert response.status_code == 404, response.text


def test_foreign_project_is_forbidden(world):
    db = world["db"]
    client = world["client"]
    other_project = world["other_project"]

    proposal = _add_proposal(
        db,
        other_project.id,
        amount=Decimal("100.00"),
        counterparty="ООО Ромашка",
        payment_purpose="Оплата",
    )
    db.commit()

    response = client.get(
        f"/projects/{other_project.id}/invoice-extraction-proposals/{proposal.id}/plan-line-suggestions"
    )
    assert response.status_code in (403, 404), response.text
