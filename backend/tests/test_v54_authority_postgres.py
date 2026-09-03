"""Opt-in PostgreSQL proof for authority locks and migration.

SQLite results must never be reported as concurrency evidence.
"""
import os
from threading import Event, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models
from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.database import Base
from test_v54_authority import seed_authority
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW


def safe_url(name):
    value = os.getenv(name)
    if not value:
        pytest.skip(f"CONDITIONAL: {name} is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db"}
    assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
    return value


def test_postgres_role_change_linearizes_before_dispatch_check():
    url = safe_url("PUW_V54_AUTHORITY_DATABASE_URL")
    schema = "v54_authority_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, hide_parameters=True, connect_args={
        "connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000",
    })
    sessions = sessionmaker(engine, expire_on_commit=False)
    changed, contender_started = Event(), Event()
    outcomes = []
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            seed_authority(db)
        resolver = AuthorityResolver(clock=lambda: NOW)

        def revoke():
            with sessions.begin() as db:
                resolver.change(
                    db, scope=scope(), principal_id=3, membership_role="viewer",
                    permissions=["metadata"], state="revoked", expected_epoch=1,
                )
                changed.set()
                assert contender_started.wait(5)
            outcomes.append("revoked")

        def dispatch_check():
            assert changed.wait(5)
            contender_started.set()
            try:
                with sessions.begin() as db:
                    resolver.require(db, scope(3), "action.approve", NOW, lock=True)
            except AuthorityDenied:
                outcomes.append("denied")
            else:
                outcomes.append("allowed")

        first = Thread(target=revoke)
        second = Thread(target=dispatch_check)
        first.start()
        second.start()
        first.join(10)
        second.join(10)
        assert not first.is_alive() and not second.is_alive()
        assert sorted(outcomes) == ["denied", "revoked"]
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_migration_upgrade_and_single_head(monkeypatch):
    url = safe_url("PUW_V54_AUTHORITY_MIGRATION_DATABASE_URL")
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    assert not inspect(engine).get_table_names(), "Refuse nonempty test database"
    backend = __import__("pathlib").Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(cfg, "head")
        with engine.connect() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a02"
            assert "v54_authority_states" in inspect(db).get_table_names()
        command.downgrade(cfg, "a54f001c0a01")
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()
