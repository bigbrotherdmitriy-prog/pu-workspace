"""Opt-in dirty PostgreSQL migration acceptance for finance source pins.

Set PUW_INVOICE_TEST_DATABASE_URL to a disposable PostgreSQL database named
``puw_invoice_test*``. The test creates and drops its own isolated schema.
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def _test_url() -> str:
    value = os.getenv("PUW_FINANCE_PIN_TEST_DATABASE_URL")
    if not value and os.getenv("PU_TEST_POSTGRES") == "1":
        value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: isolated finance-pin PostgreSQL DSN is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres", "invoice-db"} or (
        parsed.host or ""
    ).startswith("puw-invoice-test-db-")
    database = parsed.database or ""
    assert (
        database.startswith("puw_finance_pin_test")
        or database.startswith("puw_invoice_test")
        or (os.getenv("PU_TEST_POSTGRES") == "1" and database == "pu_workspace_test")
    ) and not parsed.query
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def test_dirty_postgres_migration_preserves_legacy_rows_and_enforces_pin_pairs(monkeypatch):
    url = _test_url()
    schema = "finance_pin_" + uuid4().hex
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
        command.upgrade(config, "b89e7f9a5a67")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9201,'Finance pin org')"))
            connection.execute(text(
                "INSERT INTO projects(id,name,organization_id) VALUES (9201,'Finance pin project',9201)"
            ))
            connection.execute(text(
                "INSERT INTO documents(id,project_id,name,source,status,current_version) "
                "VALUES (9201,9201,'legacy.pdf','local_upload','analyzed',1)"
            ))
            connection.execute(text(
                "INSERT INTO document_versions(id,document_id,version_number,content) "
                "VALUES (9201,9201,1,'legacy content')"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines "
                "(id,project_id,category,description,planned_amount,forecast_amount,status) "
                "VALUES (9201,9201,'Прямые','Legacy budget',100,100,'proposed')"
            ))
            connection.execute(text(
                "INSERT INTO acceptance_acts "
                "(id,project_id,document_id,number,title,amount,currency,status) "
                "VALUES (9201,9201,9201,'A-LEGACY','Legacy act',100,'RUB','proposed')"
            ))

        command.upgrade(config, "head")

        inspector = inspect(engine)
        budget_columns = {column["name"] for column in inspector.get_columns("budget_lines")}
        act_columns = {column["name"] for column in inspector.get_columns("acceptance_acts")}
        assert {
            "source_document_id", "source_document_version_id", "source_document_sha256",
        } <= budget_columns
        assert {"source_document_version_id", "source_document_sha256"} <= act_columns
        assert "ck_budget_line_source_pin_pair" in {
            constraint["name"] for constraint in inspector.get_check_constraints("budget_lines")
        }
        assert "ck_acceptance_act_source_pin_pair" in {
            constraint["name"] for constraint in inspector.get_check_constraints("acceptance_acts")
        }
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT source_document_id,source_document_version_id,source_document_sha256 "
                "FROM budget_lines WHERE id=9201"
            )).one() == (None, None, None)
            assert connection.execute(text(
                "SELECT document_id,source_document_version_id,source_document_sha256 "
                "FROM acceptance_acts WHERE id=9201"
            )).one() == (9201, None, None)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE acceptance_acts SET source_document_version_id=9201 WHERE id=9201"
                ))
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
