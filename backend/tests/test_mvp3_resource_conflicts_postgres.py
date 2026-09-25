"""Opt-in PostgreSQL gates for V3-04b resource conflicts and migration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import management as api
from app.database import Base
from app.models.management import BookableResource, Meeting
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.schema import CURRENT_SCHEMA_REVISION


BACKEND = Path(__file__).resolve().parents[1]


def _postgres_url():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 resource gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


@pytest.fixture
def resource_pg_world():
    base = create_engine(_postgres_url(), connect_args={"connect_timeout": 5})
    schema = "mvp3_resources_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        organization = Organization(name="V3-04b tenant")
        db.add(organization); db.flush()
        actor = User(name="V3-04b manager", email=f"resource-{uuid4().hex}@example.test")
        db.add(actor); db.flush()
        project = Project(name="V3-04b project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=actor.id, role="manager"))
        resource = BookableResource(
            organization_id=organization.id, managing_project_id=project.id,
            created_by_user_id=actor.id, kind="room", name="Race room",
            timezone="Europe/Moscow", capacity=10, active=True,
        )
        db.add(resource); db.flush()
        ids = actor.id, project.id, resource.id
    try:
        yield sessions, ids
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_postgres_concurrent_resource_booking_serializes_warning(resource_pg_world):
    sessions, (actor_id, project_id, resource_id) = resource_pg_world
    barrier = Barrier(2)

    def create(title):
        with sessions() as db:
            actor = db.get(User, actor_id)
            payload = api.MeetingCreate(
                project_id=project_id, title=title,
                scheduled_at=datetime(2026, 9, 26, 10, tzinfo=timezone.utc),
                duration_minutes=60, resource_ids=[resource_id],
            )
            barrier.wait(timeout=10)
            return api.create_meeting(payload, db, actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, "Concurrent A")
        second = pool.submit(create, "Concurrent B")
        outcomes = [first.result(timeout=25), second.result(timeout=25)]

    assert sorted(row["has_conflicts"] for row in outcomes) == [False, True]
    assert sum(row["conflict_count"] for row in outcomes) == 1
    with sessions() as db:
        assert db.query(Meeting).count() == 2


def _migration_config():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_postgres_upgrade_preserves_nonempty_meetings(monkeypatch):
    base_url = _postgres_url()
    base = create_engine(base_url, connect_args={"connect_timeout": 5})
    schema = "mvp3_resource_migration_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", schema_url.render_as_string(hide_password=False))
    config = _migration_config()
    try:
        command.upgrade(config, "a72d4e6f8b91")
        engine = create_engine(schema_url, connect_args={"connect_timeout": 5})
        with Session(engine) as db:
            organization = Organization(name="Migration tenant")
            db.add(organization); db.flush()
            actor = User(name="Migration manager", email=f"migration-{uuid4().hex}@example.test")
            db.add(actor); db.flush()
            # The schema is intentionally pinned to an historical revision.
            # Insert only columns that existed at that revision instead of using
            # the current ORM mapper (which now also includes projects.currency).
            project_id = db.scalar(text(
                "INSERT INTO projects (name, organization_id) "
                "VALUES (:name, :organization_id) RETURNING id"
            ), {"name": "Migration project", "organization_id": organization.id})
            meeting = Meeting(project_id=project_id, created_by_user_id=actor.id, title="Legacy meeting")
            db.add(meeting); db.commit()
            meeting_id = meeting.id
        engine.dispose()

        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        engine = create_engine(schema_url, connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            assert connection.scalar(text("SELECT title FROM meetings WHERE id=:id"), {"id": meeting_id}) == "Legacy meeting"
            assert {"bookable_resources", "meeting_resources"} <= set(inspect(connection).get_table_names())
        engine.dispose()
    finally:
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()
