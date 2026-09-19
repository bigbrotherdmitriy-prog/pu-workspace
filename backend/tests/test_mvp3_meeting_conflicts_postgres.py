"""Opt-in PostgreSQL gate for V3-04a meeting conflict projection."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import management as api
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def meeting_pg_engine():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 meeting gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    base = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "mvp3_meetings_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def _payload(project_id: int, user_id: int, title: str, minute: int) -> api.MeetingCreate:
    return api.MeetingCreate(
        project_id=project_id,
        title=title,
        scheduled_at=datetime(2026, 9, 26, 10, minute, tzinfo=timezone.utc),
        duration_minutes=60,
        participant_user_ids=[user_id],
    )


def test_postgres_conflict_warning_is_non_blocking_and_tenant_scoped(meeting_pg_engine):
    with Session(meeting_pg_engine) as db:
        first_org = Organization(name="V3-04a tenant A")
        second_org = Organization(name="V3-04a tenant B")
        user = User(name="V3-04a manager", email=f"meeting-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([first_org, second_org, user]); db.flush()
        first_project = Project(name="V3-04a project A", organization_id=first_org.id)
        second_project = Project(name="V3-04a project B", organization_id=second_org.id)
        db.add_all([first_project, second_project]); db.flush()
        db.add_all([
            ProjectMember(project_id=first_project.id, user_id=user.id, role="manager"),
            ProjectMember(project_id=second_project.id, user_id=user.id, role="manager"),
        ]); db.commit()

        first = api.create_meeting(_payload(first_project.id, user.id, "First", 0), db, user)
        overlap = api.create_meeting(_payload(first_project.id, user.id, "Overlap", 30), db, user)
        other_tenant = api.create_meeting(_payload(second_project.id, user.id, "Other tenant", 30), db, user)

        assert first["has_conflicts"] is False
        assert overlap["has_conflicts"] is True
        assert overlap["conflict_count"] == 1
        assert overlap["conflicts"][0]["meeting_id"] == first["id"]
        assert other_tenant["has_conflicts"] is False
        assert db.query(api.Meeting).count() == 3
