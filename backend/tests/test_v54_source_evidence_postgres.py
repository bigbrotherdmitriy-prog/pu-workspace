"""Opt-in real PostgreSQL CAS. Never uses DATABASE_URL or production env files."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.core.v54_permissions import SourceEvidenceError
from app.models.v54_pilot import SourceCurrent, SourceVersion
from app.source_evidence.facade import SourceEvidenceFacade
from test_v54_source_evidence_pilot import policy, prepared, scope, R
from v54_pilot_fixture import seed, uid, NOW


def test_two_postgresql_transactions_one_source_cas_winner():
    url = os.getenv("PUW_V54_SOURCE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CONDITIONAL: explicit isolated PostgreSQL URL not supplied")
    parsed = make_url(url)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1"} or (
        os.getenv("GITHUB_ACTIONS") == "true" and parsed.host == "postgres")
    assert parsed.database and parsed.database.startswith("puw_v54_test_")
    assert not parsed.query, "Connection options must not redirect the test database"
    # All tables and data confined to a NEW random schema. Never drop public/db.
    schema = "v54_source_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    engine = create_engine(url, hide_parameters=True)
    created = False

    @event.listens_for(engine, "connect")
    def isolate(conn, _):
        cursor = conn.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.execute("SET lock_timeout TO '8s'")
        cursor.execute("SET statement_timeout TO '15s'")
        cursor.close()
        conn.commit()

    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        Base.metadata.create_all(engine)
        with Session(engine) as db, db.begin():
            seed(db)
            _, source, initial_version, _ = prepared(db)

        barrier = Barrier(2, timeout=10)

        def contender(index):
            with Session(engine) as db:
                try:
                    with db.begin():
                        # Establish two distinct backend connections before racing.
                        connection_id = db.scalar(text("SELECT pg_backend_pid()"))
                        barrier.wait()
                        result = SourceEvidenceFacade(policy(), lambda: NOW).observe(
                            db, scope=scope(), source=source,
                            identity=R("connection_identity", uid(10)),
                            namespace="synthetic-mailbox",
                            observation_key=f"synthetic-concurrent-{index}",
                            provider_revision=f"synthetic-revision-{index}")
                    return "won", connection_id, result[1].ref.id.value
                except SourceEvidenceError as exc:
                    # No SQL/parameters/DSN in assertion messages.
                    assert str(exc) == "version_conflict"
                    return "conflict", connection_id, None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(contender, (1, 2)))
        assert sorted(r[0] for r in results) == ["conflict", "won"]
        assert len({r[1] for r in results}) == 2
        winner = next(r[2] for r in results if r[0] == "won")
        with Session(engine) as db:
            assert db.get(SourceCurrent, source.ref.id.value).version_id == winner
            observations = db.scalars(select(SourceVersion).where(
                SourceVersion.source_id == source.ref.id.value)).all()
            assert {row.id for row in observations} == {initial_version.ref.id.value, winner}
    finally:
        engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
