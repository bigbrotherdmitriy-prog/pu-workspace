"""Opt-in dirty PostgreSQL migration gate for act-to-budget projection."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from test_mvp4_finance_source_pins_postgres import _test_url


def test_dirty_postgres_migration_links_acts_without_inventing_legacy_links(monkeypatch):
    url = _test_url()
    schema = "act_budget_" + uuid4().hex
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
        command.upgrade(config, "c04f1a2b3d45")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9301,'Act budget org')"))
            connection.execute(text(
                "INSERT INTO projects(id,name,organization_id) VALUES (9301,'Act budget project',9301)"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines "
                "(id,project_id,category,description,planned_amount,committed_amount,actual_amount,forecast_amount,currency,status) "
                "VALUES (9301,9301,'Works','Legacy budget',100,0,0,100,'RUB','approved')"
            ))
            connection.execute(text(
                "INSERT INTO acceptance_acts "
                "(id,project_id,number,title,amount,currency,status) "
                "VALUES (9301,9301,'A-LEGACY','Legacy act',25,'RUB','approved')"
            ))

        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert "budget_line_id" in {
            column["name"] for column in inspector.get_columns("acceptance_acts")
        }
        assert "ix_acceptance_acts_budget_line_id" in {
            index["name"] for index in inspector.get_indexes("acceptance_acts")
        }
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT budget_line_id FROM acceptance_acts WHERE id=9301"
            )).scalar_one() is None
            assert connection.execute(text(
                "SELECT actual_amount FROM budget_lines WHERE id=9301"
            )).scalar_one() == 0
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
