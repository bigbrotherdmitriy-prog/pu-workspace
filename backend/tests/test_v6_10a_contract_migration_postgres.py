"""Real upgrade/downgrade gate in a disposable, synthetic PostgreSQL schema."""

import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import organizations_contracts as contracts_api
from app.core.auth import require_user
from app.database import get_db
from app.models.organization_contract import Contract
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def commercial_migration_pg(monkeypatch):
    raw = os.getenv("PUW_CONTRACT_COMMERCIAL_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        if os.getenv("PU_TEST_POSTGRES") == "1":
            pytest.fail("PostgreSQL commercial-fields gate requires a synthetic test database")
        pytest.skip("Isolated commercial-fields PostgreSQL gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "db", "postgres", "invoice-db"}
    assert url.database and "test" in url.database and not url.query
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "contract_commercial_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "d021a6c0b002")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9101,'Synthetic commercial tenant')"))
            connection.execute(text("INSERT INTO users(id,name,email,is_admin) VALUES (9101,'Synthetic owner','commercial@example.test',false)"))
            connection.execute(text("INSERT INTO projects(id,name,organization_id) VALUES (9101,'Synthetic commercial project',9101)"))
            connection.execute(text(
                "INSERT INTO contracts(id,project_id,number,title,contract_kind,amount,advance_amount,retention_percent,"
                "signed_at,warranty_until,status,record_version) VALUES "
                "(9101,9101,'S-LEGACY','Synthetic legacy','customer',1000.01,200.00,22.00,'2026-01-01','2027-01-01','draft',6)"
            ))
            connection.execute(text(
                "INSERT INTO contract_versions(contract_id,project_id,sequence,event,resulting_record_version,snapshot,changed_fields,actor_user_id) "
                "VALUES(9101,9101,1,'created',6,CAST('{\"amount\":\"1000.01\",\"retention_percent\":\"22.00\"}' AS json),CAST('[\"amount\"]' AS json),9101)"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines(id,project_id,category,description,planned_amount,forecast_amount,status) "
                "VALUES (9101,9101,'Synthetic','Synthetic legacy budget',1000.01,1000.01,'proposed')"
            ))
        yield engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _legacy_values(engine):
    with engine.connect() as connection:
        return (
            connection.execute(text("SELECT amount,advance_amount,retention_percent,signed_at,warranty_until,record_version FROM contracts WHERE id=9101")).one(),
            connection.execute(text("SELECT snapshot::text,changed_fields::text,resulting_record_version FROM contract_versions WHERE contract_id=9101")).one(),
            connection.execute(text("SELECT planned_amount,forecast_amount,status FROM budget_lines WHERE id=9101")).one(),
        )


def test_postgres_upgrade_preserves_legacy_money_history_and_default_only_downgrade(commercial_migration_pg):
    engine, config = commercial_migration_pg
    before = _legacy_values(engine)
    command.upgrade(config, "d021a6c0b003")
    assert _legacy_values(engine) == before
    with engine.connect() as connection:
        assert connection.execute(text("SELECT vat_mode,vat_rate,performed_from,performed_to FROM contracts WHERE id=9101")).one() == ("unspecified", None, None, None)
        assert connection.scalar(text("SELECT vat_snapshot IS NULL FROM budget_lines WHERE id=9101")) is True
    command.downgrade(config, "d021a6c0b002")
    assert "vat_mode" not in {column["name"] for column in inspect(engine).get_columns("contracts")}
    assert "vat_snapshot" not in {column["name"] for column in inspect(engine).get_columns("budget_lines")}
    assert _legacy_values(engine) == before


@pytest.mark.parametrize("statement", [
    "UPDATE contracts SET vat_mode='none' WHERE id=9101",
    "UPDATE contracts SET vat_mode='rate',vat_rate=0 WHERE id=9101",
    "UPDATE contracts SET performed_from='2026-01-01' WHERE id=9101",
    "UPDATE contracts SET performed_to='2026-12-31' WHERE id=9101",
    "UPDATE budget_lines SET vat_snapshot=CAST('{}' AS jsonb) WHERE id=9101",
    "UPDATE budget_lines SET vat_snapshot=CAST('null' AS jsonb) WHERE id=9101",
])
def test_postgres_downgrade_refuses_populated_new_data(commercial_migration_pg, statement):
    engine, config = commercial_migration_pg
    command.upgrade(config, "d021a6c0b003")
    with engine.begin() as connection:
        connection.execute(text(statement))
        if "CAST('null' AS jsonb)" in statement:
            assert connection.scalar(text(
                "SELECT vat_snapshot IS NOT NULL AND jsonb_typeof(vat_snapshot)='null' "
                "FROM budget_lines WHERE id=9101"
            )) is True
    with pytest.raises(RuntimeError, match="CONTRACT_COMMERCIAL_DOWNGRADE_BLOCKED"):
        command.downgrade(config, "d021a6c0b002")
    assert "vat_mode" in {column["name"] for column in inspect(engine).get_columns("contracts")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "d021a6c0b003"


@pytest.mark.parametrize("statement", [
    "UPDATE contracts SET vat_mode='rate' WHERE id=9101",
    "UPDATE contracts SET vat_mode='none',vat_rate=0 WHERE id=9101",
    "UPDATE contracts SET vat_mode='rate',vat_rate=100.01 WHERE id=9101",
    "UPDATE contracts SET performed_from='2026-02-01',performed_to='2026-01-01' WHERE id=9101",
])
def test_postgres_constraints_reject_invalid_states(commercial_migration_pg, statement):
    engine, config = commercial_migration_pg
    command.upgrade(config, "d021a6c0b003")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(statement))


def test_postgres_http_vat_date_edit_keeps_huge_contract_money_exact(commercial_migration_pg):
    engine, config = commercial_migration_pg
    command.upgrade(config, "d021a6c0b003")
    amount, advance = "1234567890123456.78", "123456789012345.67"
    with Session(engine) as db:
        user = db.get(User, 9101)
        db.add(ProjectMember(project_id=9101, user_id=user.id, role="owner"))
        row = db.get(Contract, 9101)
        row.amount, row.advance_amount = Decimal(amount), Decimal(advance)
        db.commit()
        app = FastAPI()
        app.include_router(contracts_api.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_user] = lambda: user
        with TestClient(app) as client:
            response = client.get("/projects/9101/contracts")
            assert response.status_code == 200
            loaded = response.json()["contracts"][0]
            assert (loaded["amount"], loaded["advance_amount"]) == (amount, advance)
            # The real editor resends loaded money when changing only VAT/dates.
            saved = client.patch("/projects/9101/contracts/9101", json={
                "expected_record_version": loaded["record_version"],
                "amount": loaded["amount"], "advance_amount": loaded["advance_amount"],
                "vat_mode": "none", "vat_rate": None, "performed_to": "2027-02-03",
            })
            assert saved.status_code == 200, saved.text
            assert (saved.json()["amount"], saved.json()["advance_amount"]) == (amount, advance)
        db.expire_all()
        row = db.get(Contract, 9101)
        assert (row.amount, row.advance_amount) == (Decimal(amount), Decimal(advance))
        assert row.vat_mode == "none" and row.performed_to.isoformat() == "2027-02-03"


def test_postgres_jsonb_snapshot_preserves_full_row_noop_and_vat_change_revision(commercial_migration_pg):
    engine, config = commercial_migration_pg
    command.upgrade(config, "d021a6c0b003")
    for table in ("budget_lines", "cash_flow_entries", "acceptance_acts", "payment_events",
                  "contract_budget_proposals", "invoice_extraction_proposals"):
        column = next(column for column in inspect(engine).get_columns(table) if column["name"] == "vat_snapshot")
        assert str(column["type"]) == "JSONB"
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO cash_flow_entries(id,project_id,title,direction,planned_date,"
            "planned_amount,actual_amount,currency,status) VALUES "
            "(9101,9101,'Synthetic VAT revision','outflow','2026-01-01',71.23,0,'RUB','proposed')"
        ))

    def revision():
        with engine.connect() as connection:
            return connection.scalar(text("SELECT cash_flow_revision FROM projects WHERE id=9101"))

    before = revision()
    # Every write below is a separate committed transaction: the trigger's txid
    # deduplication must not hide an unintended bump or suppress a real change.
    with engine.begin() as connection:
        connection.execute(text("UPDATE cash_flow_entries SET title=title WHERE id=9101"))
    assert revision() == before
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE cash_flow_entries SET vat_snapshot=CAST('{\"mode\":\"rate\",\"rate\":\"0.00\"}' AS jsonb) WHERE id=9101"
        ))
    assert revision() == before + 1
    with engine.begin() as connection:
        connection.execute(text("UPDATE cash_flow_entries SET title=title WHERE id=9101"))
    assert revision() == before + 1
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE cash_flow_entries SET vat_snapshot=CAST('{\"rate\":\"0.00\",\"mode\":\"rate\"}' AS jsonb) WHERE id=9101"
        ))
    assert revision() == before + 1
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE cash_flow_entries SET vat_snapshot=CAST('{\"mode\":\"none\",\"rate\":null}' AS jsonb) WHERE id=9101"
        ))
    assert revision() == before + 2
