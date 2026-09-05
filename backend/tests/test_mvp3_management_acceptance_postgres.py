"""Opt-in PostgreSQL concurrency proof for MVP3 acceptance.

The test refuses ordinary databases and confines all objects to a random schema.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models.management import Obligation
from app.models.project_member import ProjectMember
from app.models.v54_pilot import SourceReference
from app.mvp3.lifecycle import ManagementConflict, ManagementLifecycle
from v54_pilot_fixture import pin, seed, uid


def test_postgresql_obligation_cas_has_one_winner():
    url = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CONDITIONAL: explicit isolated MVP3 PostgreSQL URL not supplied")
    parsed = make_url(url)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1"} or (
        os.getenv("GITHUB_ACTIONS") == "true" and parsed.host == "postgres"
    )
    assert parsed.database and parsed.database.startswith("puw_mvp3_test_")
    assert not parsed.query, "Connection options must not redirect the test database"

    schema = "mvp3_acceptance_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    engine = create_engine(url, hide_parameters=True)
    created = False

    @event.listens_for(engine, "connect")
    def isolate(connection, _record):
        cursor = connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.execute("SET lock_timeout TO '8s'")
        cursor.execute("SET statement_timeout TO '15s'")
        cursor.close()
        connection.commit()

    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        Base.metadata.create_all(engine)
        with Session(engine) as db, db.begin():
            seed(db)
            source = db.get(SourceReference, uid(13))
            source.availability = "available"
            source.freshness = "fresh"
            source.sync_state = "current"
            db.add(ProjectMember(project_id=4, user_id=2, role="manager"))
            service = ManagementLifecycle()
            scope = service.scope(db, project_id=4, actor_user_id=2)
            row = service.create_obligation(
                db,
                scope=scope,
                title="Synthetic concurrent obligation",
                owner_user_id=2,
                evidence_pins=[pin("evidence", uid(16), tenant=1)],
                due_date=date(2026, 9, 30),
            )
            obligation_id = row.id

        barrier = Barrier(2, timeout=10)

        def contender():
            with Session(engine) as db:
                service = ManagementLifecycle()
                scope = service.scope(db, project_id=4, actor_user_id=2)
                backend_pid = db.scalar(text("SELECT pg_backend_pid()"))
                barrier.wait()
                try:
                    with db.begin_nested():
                        row = service.transition_obligation(
                            db,
                            scope=scope,
                            obligation_id=obligation_id,
                            expected_version=1,
                            status="confirmed",
                        )
                    db.commit()
                    return "won", backend_pid, row.record_version
                except ManagementConflict as exc:
                    db.rollback()
                    assert str(exc) == "version_conflict"
                    return "conflict", backend_pid, None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: contender(), (1, 2)))
        assert sorted(item[0] for item in results) == ["conflict", "won"]
        assert len({item[1] for item in results}) == 2
        with Session(engine) as db:
            row = db.get(Obligation, obligation_id)
            assert row.record_version == 2 and row.status == "confirmed"
    finally:
        engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
