"""Real upgrade/downgrade gate for ADR-GPR-PER-CONTRACT-BUDGET-ALLOCATION-RU's
schedule_budget_links table, in a disposable, synthetic PostgreSQL schema.
Manually verified against a real PostgreSQL 16 container before this file was
written (the CHECK constraint, FK cascade deletes, and both downgrade paths)."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def schedule_budget_links_migration_pg(monkeypatch):
    raw = os.getenv("PUW_SCHEDULE_BUDGET_LINKS_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        if os.getenv("PU_TEST_POSTGRES") == "1":
            pytest.fail("PostgreSQL schedule-budget-links gate requires a synthetic test database")
        pytest.skip("Isolated schedule-budget-links PostgreSQL gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "db", "postgres", "invoice-db"}
    assert url.database and "test" in url.database and not url.query
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "schedule_budget_links_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "fde452722a0f")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,name,email,is_admin) VALUES (9101,'U','u@example.test',false)"))
            connection.execute(text("INSERT INTO projects(id,name,organization_id) VALUES (9101,'P',1)"))
            connection.execute(text(
                "INSERT INTO contracts(id,project_id,number,title,contract_kind,status,record_version) "
                "VALUES (9101,9101,'C1','C','supply','active',1)"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines(id,line_kind,direction,category,description,planned_amount,"
                "committed_amount,actual_amount,forecast_amount,currency,status,project_id,contract_id,record_version) "
                "VALUES (9101,'contract_control','outflow','Control','C',1000,0,0,1000,'RUB','approved',9101,9101,1)"
            ))
            connection.execute(text(
                "INSERT INTO schedule_baselines(id,project_id,contract_id,created_by_user_id,name,version,status) "
                "VALUES (9101,9101,9101,9101,'GPR',1,'approved')"
            ))
            connection.execute(text(
                "INSERT INTO schedule_items(id,project_id,baseline_id,title,sort_order,duration_days) "
                "VALUES (9101,9101,9101,'Stage1',1,1)"
            ))
        yield engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_check_constraint_rejects_zero_and_negative_amounts(schedule_budget_links_migration_pg):
    engine, config = schedule_budget_links_migration_pg
    command.upgrade(config, "f2da35389553")
    for bad_amount in ("0", "-1"):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO schedule_budget_links(budget_line_id,schedule_item_id,amount,created_by_user_id) "
                    "VALUES (9101,9101,:amount,9101)"
                ), {"amount": bad_amount})


def test_postgres_cascade_deletes_the_link_when_either_side_is_deleted(schedule_budget_links_migration_pg):
    engine, config = schedule_budget_links_migration_pg
    command.upgrade(config, "f2da35389553")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schedule_budget_links(id,budget_line_id,schedule_item_id,amount,created_by_user_id) "
            "VALUES (1,9101,9101,400,9101)"
        ))
        connection.execute(text("DELETE FROM schedule_items WHERE id=9101"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM schedule_budget_links")) == 0


def test_postgres_downgrade_blocked_while_allocations_exist(schedule_budget_links_migration_pg):
    engine, config = schedule_budget_links_migration_pg
    command.upgrade(config, "f2da35389553")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schedule_budget_links(budget_line_id,schedule_item_id,amount,created_by_user_id) "
            "VALUES (9101,9101,400,9101)"
        ))
    with pytest.raises(RuntimeError, match="SCHEDULE_BUDGET_LINKS_DOWNGRADE_BLOCKED"):
        command.downgrade(config, "fde452722a0f")
    assert "schedule_budget_links" in inspect(engine).get_table_names()


def test_postgres_downgrade_succeeds_once_table_is_empty(schedule_budget_links_migration_pg):
    engine, config = schedule_budget_links_migration_pg
    command.upgrade(config, "f2da35389553")
    command.downgrade(config, "fde452722a0f")
    assert "schedule_budget_links" not in inspect(engine).get_table_names()
