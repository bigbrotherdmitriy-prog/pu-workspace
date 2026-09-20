"""Opt-in PostgreSQL proof for tenant filtering and stable attention pagination."""

import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.management import _meeting_payload
from app.attention_read_model import build_attention_feed
from app.database import Base
from app.models.management import Obligation
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def _postgres_url():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 attention gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


@pytest.fixture
def attention_pg_world():
    base = create_engine(_postgres_url(), connect_args={"connect_timeout": 5})
    schema = "mvp3_attention_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db:
        first_org, second_org = Organization(name="First"), Organization(name="Second")
        db.add_all([first_org, second_org]); db.flush()
        viewer = User(name="Viewer", email=f"viewer-{uuid4().hex}@example.test")
        other = User(name="Other", email=f"other-{uuid4().hex}@example.test")
        db.add_all([viewer, other]); db.flush()
        project = Project(name="Visible", organization_id=first_org.id)
        foreign = Project(name="Foreign", organization_id=second_org.id)
        db.add_all([project, foreign]); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
        for sequence, target, owner in [
            (1, project, viewer), (2, project, viewer), (3, project, viewer),
            (4, foreign, other),
        ]:
            db.add(Obligation(
                project_id=target.id, owner_user_id=owner.id, title=f"O{sequence}",
                status="needs_confirmation", due_date=date(2026, 10, sequence),
                source_type="message", source_id=f"m:{sequence}", source_name=f"m{sequence}",
                source_excerpt=f"e{sequence}", source_hash=f"{sequence:064x}", confidence=.9,
            ))
        ids = viewer.id, project.id
    try:
        yield sessions, ids
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_postgres_attention_cursor_and_tenant_scope(attention_pg_world):
    sessions, (viewer_id, project_id) = attention_pg_world
    with sessions() as db:
        viewer = db.get(User, viewer_id)
        first = build_attention_feed(
            db, viewer, meeting_payload=_meeting_payload, kind="obligation", limit=2,
        )
        second = build_attention_feed(
            db, viewer, meeting_payload=_meeting_payload, kind="obligation", limit=2,
            cursor=first["next_cursor"],
        )

    assert [item["project_id"] for item in first["items"] + second["items"]] == [project_id] * 3
    assert len({item["entity_id"] for item in first["items"] + second["items"]}) == 3
    assert first["next_cursor"] is not None
    assert second["next_cursor"] is None
