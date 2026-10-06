"""Real upgrade/downgrade gate for ADR-V6-05-INCOME-BUDGET-RU's direction column,
in a disposable, synthetic PostgreSQL schema. Manually verified against a real
PostgreSQL 16 container before this file was written (backfill of all three
line_kind cases, the CHECK constraint, and both downgrade paths)."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def budget_direction_migration_pg(monkeypatch):
    raw = os.getenv("PUW_BUDGET_LINE_DIRECTION_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        if os.getenv("PU_TEST_POSTGRES") == "1":
            pytest.fail("PostgreSQL budget-line-direction gate requires a synthetic test database")
        pytest.skip("Isolated budget-line-direction PostgreSQL gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "db", "postgres", "invoice-db"}
    assert url.database and "test" in url.database and not url.query
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "budget_direction_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "d021a6c0b003")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,name,email,is_admin) VALUES (9101,'U','u@example.test',false)"))
            connection.execute(text("INSERT INTO projects(id,name,organization_id) VALUES (9101,'P',1)"))
            connection.execute(text(
                "INSERT INTO contracts(id,project_id,number,title,contract_kind,status,record_version) VALUES "
                "(9101,9101,'C1','Customer','customer','active',1), "
                "(9102,9101,'C2','Supply','supply','active',1), "
                "(9103,9101,'C3','Prime','prime_reference','active',1)"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines(id,line_kind,category,description,planned_amount,committed_amount,"
                "actual_amount,forecast_amount,currency,status,project_id,contract_id,record_version) VALUES "
                "(9101,'analytical_expense','Materials','Expense line',100,0,0,100,'RUB','proposed',9101,9102,1), "
                "(9102,'contract_control','Control','Customer control',200,0,0,200,'RUB','proposed',9101,9101,1), "
                "(9103,'contract_control','Control','Supply control',150,0,0,150,'RUB','proposed',9101,9102,1), "
                "(9104,'contract_control','Control','Prime control',50,0,0,50,'RUB','proposed',9101,9103,1), "
                "(9105,'legacy_unclassified','Old','Legacy row',10,0,0,10,'RUB','proposed',9101,9102,1), "
                "(9106,'contract_control','Control','No contract',1,0,0,1,'RUB','proposed',9101,NULL,1)"
            ))
        yield engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _directions(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, direction FROM budget_lines WHERE id >= 9101 ORDER BY id")).all()
        return {row.id: row.direction for row in rows}


def test_postgres_backfill_derives_direction_per_line_kind_and_contract_role(budget_direction_migration_pg):
    engine, config = budget_direction_migration_pg
    command.upgrade(config, "fde452722a0f")
    assert _directions(engine) == {
        9101: "outflow",  # analytical_expense: always outflow by construction
        9102: "inflow",   # contract_control on a customer contract
        9103: "outflow",  # contract_control on a supply contract
        9104: None,       # contract_control on prime_reference: no cash-flow role
        9105: None,       # legacy_unclassified: stays NULL until manual review (decision 4)
        9106: None,       # contract_control with no contract: nothing to derive from
    }


def test_postgres_constraint_rejects_any_value_outside_inflow_outflow(budget_direction_migration_pg):
    engine, config = budget_direction_migration_pg
    command.upgrade(config, "fde452722a0f")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE budget_lines SET direction='sideways' WHERE id=9101"))


def test_postgres_downgrade_blocked_while_income_data_exists(budget_direction_migration_pg):
    engine, config = budget_direction_migration_pg
    command.upgrade(config, "fde452722a0f")
    with pytest.raises(RuntimeError, match="BUDGET_LINE_DIRECTION_DOWNGRADE_BLOCKED"):
        command.downgrade(config, "d021a6c0b003")
    assert "direction" in {column["name"] for column in inspect(engine).get_columns("budget_lines")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "fde452722a0f"


def test_postgres_downgrade_succeeds_once_income_rows_are_gone(budget_direction_migration_pg):
    engine, config = budget_direction_migration_pg
    command.upgrade(config, "fde452722a0f")
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM budget_lines WHERE direction='inflow'"))
    command.downgrade(config, "d021a6c0b003")
    assert "direction" not in {column["name"] for column in inspect(engine).get_columns("budget_lines")}
