from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.execution_finance import (
    CashFlowEntry,
    InvoiceExtractionProposal,
)
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User
from app.morning_digest import (
    build_morning_digest,
    run_morning_digest_job,
    schedule_morning_digest,
)


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Morning org")
        user = User(name="Owner", email="owner-morning@example.test", is_admin=False)
        db.add_all([org, user])
        db.flush()
        project = Project(name="Morning project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.commit()
        yield {"db": db, "org": org, "user": user, "project": project}
    engine.dispose()


def _add_overdue(db, project_id, *, amount: str = "300.00") -> CashFlowEntry:
    entry = CashFlowEntry(
        project_id=project_id,
        direction="outflow",
        title="Счёт",
        planned_date=date(2020, 1, 10),
        actual_date=None,
        planned_amount=Decimal(amount),
        actual_amount=Decimal("0"),
        invoiced_amount=Decimal(amount),
        currency="RUB",
        status="approved",
    )
    db.add(entry)
    db.flush()
    return entry


def _add_proposal(db, project_id, *, status: str = "proposed") -> InvoiceExtractionProposal:
    proposal = InvoiceExtractionProposal(
        project_id=project_id,
        source_document_id=1,
        source_document_version_id=1,
        source_document_sha256="0" * 64,
        extraction_method="manual",
        status=status,
        target_kind="cash_flow",
        currency="RUB",
    )
    db.add(proposal)
    db.flush()
    return proposal


def test_overdue_and_proposal_in_digest(world):
    db = world["db"]
    project = world["project"]
    _add_overdue(db, project.id)
    _add_proposal(db, project.id, status="proposed")
    db.commit()

    text = build_morning_digest(db, today=date(2026, 10, 21))
    assert text is not None
    assert "Просрочено счетов: 1 на 300 ₽" in text
    assert "Подтвердить счетов: 1" in text
    assert "☀️ Сводка на 21.10.2026" in text


def test_empty_project_not_included(world):
    db = world["db"]
    text = build_morning_digest(db, today=date(2026, 10, 21))
    assert text is None


def test_schedule_before_hour_no_job(world):
    db = world["db"]
    captured: list[tuple] = []

    class _Job:
        status = "queued"
        attempts = 0

    def fake_enqueue(db_, kind, payload, idempotency_key=None):
        captured.append((kind, payload, idempotency_key))
        return _Job()

    with patch("app.morning_digest.enqueue", fake_enqueue), \
            patch("app.morning_digest._enabled", lambda: True):
        now = datetime(2026, 10, 21, 8, 59, tzinfo=timezone.utc).astimezone(
            __import__("zoneinfo").ZoneInfo("Europe/Moscow")
        )
        # Build a UTC instant that corresponds to 08:59 MSK
        from zoneinfo import ZoneInfo
        msk = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 10, 21, 8, 59, tzinfo=msk).astimezone(timezone.utc)
        assert schedule_morning_digest(db, now=now) == 0
        assert captured == []

        now9 = datetime(2026, 10, 21, 9, 0, tzinfo=msk).astimezone(timezone.utc)
        assert schedule_morning_digest(db, now=now9) == 1
        assert len(captured) == 1
        kind, payload, key = captured[0]
        assert kind == "finance.morning_digest"
        assert payload == {"day": "2026-10-21"}
        assert key == "finance.morning_digest:2026-10-21"


def test_run_job_without_telegram(world):
    with patch("app.morning_digest.SessionLocal", lambda: world["db"]), \
            patch("app.integrations.telegram.telegram_configured", lambda: False):
        result = run_morning_digest_job({"day": "2026-10-21"})
    assert result.get("sent") is False
    if "reason" in result:
        assert result["reason"] == "telegram_not_configured"
