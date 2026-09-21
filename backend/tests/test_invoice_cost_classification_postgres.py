"""Opt-in PostgreSQL acceptance for invoice classification and confirmation.

Set PUW_INVOICE_TEST_DATABASE_URL to a disposable PostgreSQL database named
``puw_invoice_test*``.  Every test uses an isolated schema and drops it.
"""

import hashlib
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.api.execution_finance as finance_api
import app.models  # noqa: F401 - register every mapped model for relationships
from app.api.execution_finance import (
    InvoiceExtractionConfirm,
    InvoiceExtractionCreate,
    confirm_invoice_extraction,
    create_invoice_extraction,
    retry_invoice_extraction_ai,
)
from app.invoice_extraction import InvoiceFields
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import CashFlowEntry, CostCategory, InvoiceExtractionProposal
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def _test_url() -> str:
    value = os.getenv("PUW_INVOICE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: isolated invoice PostgreSQL DSN is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres", "invoice-db"} or (
        parsed.host or ""
    ).startswith("puw-invoice-test-db-")
    assert (parsed.database or "").startswith("puw_invoice_test") and not parsed.query
    return value


@pytest.fixture
def pg_session(monkeypatch):
    url = _test_url()
    schema = "invoice_acceptance_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    command.upgrade(config, "head")
    session = sessionmaker(bind=engine)()
    try:
        yield session, engine
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_upload_proposal_confirmation_creates_dual_written_cash_flow_on_postgres(
    pg_session, monkeypatch,
):
    db, engine = pg_session
    suffix = uuid4().hex
    user = User(name="Invoice Manager", email=f"invoice-{suffix}@example.test", is_admin=False)
    organization = Organization(name=f"Invoice Runtime {suffix}")
    db.add_all([user, organization])
    db.flush()
    project = Project(name="Invoice Runtime", organization_id=organization.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
    document = Document(
        project_id=project.id, name="invoice.pdf", source="local_upload",
        status="analyzed", current_version=1,
    )
    db.add(document)
    db.flush()
    content = "Поставщик ООО Бетон. Назначение: строительные материалы. Итого 125 400,50 руб."
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db.add(version)
    db.commit()

    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="Итого 125 400,50 руб.",
        currency="RUB", counterparty="ООО Бетон", counterparty_evidence_quote="ООО Бетон",
        payment_purpose="строительные материалы",
        payment_purpose_evidence_quote="строительные материалы",
        suggested_category_name="Прямые", category_evidence_quote="строительные материалы",
        planned_date=date(2026, 9, 20), confidence=0.96, extraction_method="llm",
    ))

    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id), db, user,
    )
    assert proposed["status"] == "proposed"
    assert db.query(CashFlowEntry).filter_by(project_id=project.id).count() == 0

    confirmed = confirm_invoice_extraction(
        proposed["id"], InvoiceExtractionConfirm(), db, user,
    )
    entry = db.get(CashFlowEntry, confirmed["created_cash_flow_id"])
    category = db.get(CostCategory, entry.cost_category_id)
    assert confirmed["status"] == "confirmed"
    assert category.name == "Прямые"
    assert entry.category == category.name
    assert entry.planned_amount == Decimal("125400.50")
    assert entry.source_document_id == document.id
    assert entry.source_document_version_id == version.id
    assert entry.source_document_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert inspect(engine).get_table_names().count("invoice_extraction_proposals") == 1


def test_temporary_fallback_retry_updates_same_postgres_proposal(pg_session, monkeypatch):
    db, _engine = pg_session
    suffix = uuid4().hex
    user = User(name="Invoice Editor", email=f"invoice-retry-{suffix}@example.test", is_admin=False)
    organization = Organization(name=f"Invoice Retry {suffix}")
    db.add_all([user, organization])
    db.flush()
    project = Project(name="Invoice Retry", organization_id=organization.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="editor"))
    document = Document(
        project_id=project.id, name="invoice-retry.pdf", source="local_upload",
        status="analyzed", current_version=1,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        document_id=document.id, version_number=1,
        content="Поставщик ООО Бетон. Материалы. Итого 125 400,50 руб.",
    )
    db.add(version)
    db.commit()
    fallback = InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.",
        currency="RUB", counterparty=None, counterparty_evidence_quote=None,
        payment_purpose=None, payment_purpose_evidence_quote=None,
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=None, confidence=0.35, extraction_method="regex",
        fallback_reason="temporarily_unavailable",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: fallback)
    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id), db, user,
    )
    llm = InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.",
        currency="RUB", counterparty="ООО Бетон", counterparty_evidence_quote="ООО Бетон",
        payment_purpose="Материалы", payment_purpose_evidence_quote="Материалы",
        suggested_category_name="Прямые", category_evidence_quote="Материалы",
        planned_date=date(2026, 9, 22), confidence=0.95, extraction_method="llm",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: llm)

    retried = retry_invoice_extraction_ai(proposed["id"], db, user)

    stored = db.get(InvoiceExtractionProposal, proposed["id"])
    assert retried["id"] == proposed["id"] == stored.id
    assert stored.source_document_version_id == version.id
    assert stored.extraction_method == "llm" and stored.fallback_reason is None
    assert stored.counterparty == "ООО Бетон"
    assert db.query(InvoiceExtractionProposal).count() == 1
    assert db.query(CashFlowEntry).count() == 0


def test_dirty_legacy_text_categories_survive_and_are_backfilled_on_postgres(monkeypatch):
    url = _test_url()
    schema = "invoice_legacy_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "e73c2b4a901d")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9101,'Legacy invoice org')"))
            connection.execute(text(
                "INSERT INTO projects(id,name,organization_id) "
                "VALUES (9101,'Legacy invoice project',9101)"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines "
                "(id,project_id,category,description,planned_amount,forecast_amount,status) "
                "VALUES (9101,9101,'Аренда','Legacy rent',1000,1000,'proposed')"
            ))
            connection.execute(text(
                "INSERT INTO cash_flow_entries "
                "(id,project_id,category,direction,title,planned_date,planned_amount,currency,status) "
                "VALUES (9101,9101,'Командировки','outflow','Legacy travel',DATE '2026-09-20',500,'RUB','proposed')"
            ))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT cc.name FROM budget_lines b JOIN cost_categories cc "
                "ON cc.id=b.cost_category_id WHERE b.id=9101"
            )) == "Аренда"
            assert connection.scalar(text(
                "SELECT cc.name FROM cash_flow_entries c JOIN cost_categories cc "
                "ON cc.id=c.cost_category_id WHERE c.id=9101"
            )) == "Командировки"
            assert connection.scalar(text(
                "SELECT count(*) FROM cost_categories WHERE organization_id=9101"
            )) == 5
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
