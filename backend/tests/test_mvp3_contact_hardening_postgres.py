"""Opt-in real PostgreSQL gate for mailbox-scoped contact decisions."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.project_contacts import ContactResolutionCommand, resolve_contact
from app.database import Base
from app.models.management import ManagementHistory
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def contact_pg_engine():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 contact concurrency gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    base = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "mvp3_contact_" + uuid4().hex
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


def _world(engine, *, contacts=1):
    with Session(engine) as db:
        organization = Organization(name="MVP3 contact PG tenant")
        user = User(name="Contact reviewer", email=f"contact-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="MVP3 contact PG project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        rows = []
        for index in range(contacts):
            row = ProjectContact(
                organization_id=organization.id, project_id=project.id,
                created_by_user_id=user.id, name=f"Contact {index}",
                email=f"contact-{index}@example.test", normalized_email=f"contact-{index}@example.test",
                normalized_domain="example.test", confirmed=False, active=True,
                source="gmail", resolution_state="proposed",
                resolution_reason_code="gmail_sender_candidate",
            )
            db.add(row); rows.append(row)
        db.commit()
        return user.id, [row.id for row in rows]


def _resolve(engine, barrier, user_id, contact_id, key, *, decision="confirm"):
    barrier.wait(timeout=10)
    try:
        with Session(engine) as db:
            return resolve_contact(
                contact_id,
                ContactResolutionCommand(
                    decision_key=key, expected_record_version=1,
                    decision=decision, reason_code="concurrency_test",
                ),
                db, db.get(User, user_id),
            )
    except HTTPException as error:
        return {"status_code": error.status_code}


def test_postgres_exact_concurrent_replay_applies_once(contact_pg_engine):
    user_id, (contact_id,) = _world(contact_pg_engine)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=20) for future in (
            pool.submit(_resolve, contact_pg_engine, barrier, user_id, contact_id, "contact-pg-exact-replay"),
            pool.submit(_resolve, contact_pg_engine, barrier, user_id, contact_id, "contact-pg-exact-replay"),
        )]
    assert sorted(result.get("record_version", 0) for result in results) == [2, 2]
    assert sum(bool(result.get("already_applied")) for result in results) == 1
    with Session(contact_pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(ManagementHistory).where(
            ManagementHistory.entity_type == "project_contact",
            ManagementHistory.idempotency_key == "contact-pg-exact-replay",
        )) == 1


def test_postgres_different_commands_have_one_cas_winner(contact_pg_engine):
    user_id, (contact_id,) = _world(contact_pg_engine)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=20) for future in (
            pool.submit(_resolve, contact_pg_engine, barrier, user_id, contact_id, "contact-pg-first"),
            pool.submit(_resolve, contact_pg_engine, barrier, user_id, contact_id, "contact-pg-second"),
        )]
    assert sorted(result.get("status_code", result.get("record_version")) for result in results) == [2, 409]


def test_postgres_decision_key_collision_across_contacts_fails_closed(contact_pg_engine):
    user_id, contact_ids = _world(contact_pg_engine, contacts=2)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=20) for future, contact_id in zip((
            pool.submit(_resolve, contact_pg_engine, barrier, user_id, contact_ids[0], "contact-pg-collision"),
            pool.submit(_resolve, contact_pg_engine, barrier, user_id, contact_ids[1], "contact-pg-collision"),
        ), contact_ids)]
    assert sorted(result.get("status_code", result.get("record_version")) for result in results) == [2, 409]
    with Session(contact_pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(ManagementHistory).where(
            ManagementHistory.entity_type == "project_contact",
            ManagementHistory.idempotency_key == "contact-pg-collision",
        )) == 1
