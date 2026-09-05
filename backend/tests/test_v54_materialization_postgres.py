"""Opt-in PostgreSQL migration and CAS proof; SQLite is not concurrency evidence."""
from dataclasses import replace
from datetime import timedelta
import os
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models
from app.database import Base
from app.core.v54_permissions import SourceEvidenceError
from app.staging.contracts import KekRef
from app.staging.lifecycle import LifecycleAuthority, MaterializationLifecycle
from test_v54_materialization_lifecycle import admitted
from test_v54_source_evidence_pilot import policy as source_policy, prepared, scope
from v54_pilot_fixture import NOW, seed


class NoEffectStorage:
    def write(self, *args, **kwargs):
        raise AssertionError("begin_write must not touch storage")

    def read_chunks(self, *args, **kwargs):
        raise AssertionError("not used")

    def delete(self, *args, **kwargs):
        raise AssertionError("not used")

    def cleanup_partials(self, *args, **kwargs):
        raise AssertionError("not used")


def safe_url():
    value = os.getenv("PUW_V54_MATERIALIZATION_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: PUW_V54_MATERIALIZATION_DATABASE_URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
    return value


def test_postgres_materialization_cas_has_one_winner():
    url = safe_url()
    schema = "v54_materialization_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, hide_parameters=True, connect_args={
        "connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000",
    })
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            seed(db)
            prepared(db)
            policy = source_policy()
            authority = LifecycleAuthority(
                policy=policy, allowed_residencies=frozenset({"eu-test"}),
                allowed_keks=frozenset({KekRef("kms/materialization", "v7")}),
                max_retention=timedelta(hours=1), derive_allowed=True,
            )
            service = MaterializationLifecycle(authority, NoEffectStorage(), lambda: NOW)
            first = admitted(service, db)
        barrier = Barrier(2)
        lock = Lock()
        outcomes = []

        def contender(fence):
            try:
                with sessions.begin() as db:
                    local = MaterializationLifecycle(authority, NoEffectStorage(), lambda: NOW)
                    barrier.wait(5)
                    local.begin_write(db, scope=scope(), materialization=first, fence=fence)
                outcome = "won"
            except SourceEvidenceError:
                outcome = "denied"
            with lock:
                outcomes.append(outcome)

        threads = [Thread(target=contender, args=(str(index) * 32,)) for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert not any(thread.is_alive() for thread in threads)
        assert sorted(outcomes) == ["denied", "won"]
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_materialization_migration_on_explicit_empty_database(monkeypatch):
    url = safe_url()
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    assert not inspect(engine).get_table_names(), "Refuse nonempty test database"
    backend = __import__("pathlib").Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a16"
            assert "v54_materializations" in inspect(connection).get_table_names()
    finally:
        engine.dispose()
