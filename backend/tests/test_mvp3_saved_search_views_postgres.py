"""Opt-in PostgreSQL migration and concurrent CAS gates for M3-10."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

import app.models  # noqa: F401
from app.api.saved_search_views import SavedSearchViewUpdate, update_saved_search_view
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.saved_search_view import SavedSearchView
from app.models.user import User
from app.schema import CURRENT_SCHEMA_REVISION

BACKEND = Path(__file__).resolve().parents[1]


def _postgres_url():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 saved-view gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


@pytest.fixture
def pg_saved_view_world():
    base = create_engine(_postgres_url(), connect_args={"connect_timeout": 5})
    schema = "mvp3_saved_view_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        organization = Organization(name="Saved view PG tenant")
        db.add(organization); db.flush()
        user = User(name="Saved view PG user", email=f"saved-view-{uuid4().hex}@example.test")
        db.add(user); db.flush()
        project = Project(name="Saved view PG project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="viewer"))
        view = SavedSearchView(
            organization_id=organization.id, project_id=project.id, owner_user_id=user.id,
            name="Initial", filters={"q": "initial", "types": ["task"]},
        )
        db.add(view); db.flush()
        ids = user.id, view.id
    try:
        yield sessions, ids
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_postgres_concurrent_saved_view_cas_has_one_winner(pg_saved_view_world):
    sessions, (user_id, view_id) = pg_saved_view_world
    barrier = Barrier(2)

    def run(name):
        with sessions() as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=10)
            try:
                result = update_saved_search_view(
                    view_id, SavedSearchViewUpdate(expected_record_version=1, name=name), db, user,
                )
                return "ok", result["record_version"]
            except HTTPException as exc:
                return "error", exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=25) for future in (
            pool.submit(run, "Winner A"), pool.submit(run, "Winner B"),
        )]
    assert sorted(results) == [("error", 409), ("ok", 2)]
    with sessions() as db:
        row = db.get(SavedSearchView, view_id)
        assert row.record_version == 2 and row.name in {"Winner A", "Winner B"}


def _migration_config():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_postgres_saved_view_upgrade_and_downgrade(monkeypatch):
    base_url = _postgres_url()
    base = create_engine(base_url, connect_args={"connect_timeout": 5})
    schema = "mvp3_saved_view_migration_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", schema_url.render_as_string(hide_password=False))
    config = _migration_config()
    try:
        command.upgrade(config, "b88e7f9a4f56")
        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        engine = create_engine(schema_url, connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            assert "saved_search_views" in inspect(connection).get_table_names()
            constraints = inspect(connection).get_check_constraints("saved_search_views")
            assert any(item["name"] == "ck_saved_search_views_record_version" for item in constraints)
        engine.dispose()
        command.downgrade(config, "b88e7f9a4f56")
        engine = create_engine(schema_url, connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            assert "saved_search_views" not in inspect(connection).get_table_names()
        engine.dispose()
        command.upgrade(config, CURRENT_SCHEMA_REVISION)
    finally:
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()
