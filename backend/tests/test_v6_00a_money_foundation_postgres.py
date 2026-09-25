"""Opt-in PostgreSQL migration proof for V6-00a project currency."""

from pathlib import Path
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from test_mvp4_finance_source_pins_postgres import _test_url


def _config(monkeypatch, url):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    return config


def _schema_url(base, schema):
    return make_url(base).update_query_dict({"options": f"-csearch_path={schema}"})


def test_postgres_migration_backfills_all_existing_projects_to_rub(monkeypatch):
    base = _test_url()
    schema = "v600a_rub_" + uuid4().hex
    admin = create_engine(base, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = _schema_url(base, schema)
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    config = _config(monkeypatch, scoped)
    try:
        command.upgrade(config, "b62f9d3a4c10")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9601,'V6-00a org')"))
            connection.execute(text(
                "INSERT INTO projects(id,name,organization_id) VALUES (9601,'Existing project',9601)"
            ))
            connection.execute(text(
                "INSERT INTO cash_flow_entries "
                "(id,project_id,direction,title,planned_date,planned_amount,actual_amount,currency,status) "
                "VALUES (9601,9601,'outflow','Existing RUB row','2026-09-25',125400.50,0,'RUB','approved')"
            ))

        command.upgrade(config, "head")

        assert "currency" in {column["name"] for column in inspect(engine).get_columns("projects")}
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT currency FROM projects WHERE id=9601"
            )).scalar_one() == "RUB"
            assert connection.execute(text(
                "SELECT planned_amount FROM cash_flow_entries WHERE id=9601"
            )).scalar_one() == Decimal("125400.50")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_migration_stops_instead_of_relabelling_non_rub_rows(monkeypatch):
    base = _test_url()
    schema = "v600a_stop_" + uuid4().hex
    admin = create_engine(base, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = _schema_url(base, schema)
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    config = _config(monkeypatch, scoped)
    try:
        command.upgrade(config, "b62f9d3a4c10")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9602,'V6-00a org')"))
            connection.execute(text(
                "INSERT INTO projects(id,name,organization_id) VALUES (9602,'Existing project',9602)"
            ))
            connection.execute(text(
                "INSERT INTO cash_flow_entries "
                "(id,project_id,direction,title,planned_date,planned_amount,actual_amount,currency,status) "
                "VALUES (9602,9602,'outflow','Foreign row','2026-09-25',1,0,'USD','approved')"
            ))

        with pytest.raises(RuntimeError, match="existing non-RUB finance rows"):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT currency FROM cash_flow_entries WHERE id=9602"
            )).scalar_one() == "USD"
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
