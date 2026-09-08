"""Opt-in PostgreSQL proof for authority locks and migration.

SQLite results must never be reported as concurrency evidence.
"""
import os
import json
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models
from app.schema import CURRENT_SCHEMA_REVISION
from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.database import Base
from test_v54_authority import seed_authority
from test_v54_source_evidence_pilot import scope
from v54_pilot_fixture import NOW

# Seed uses NOW. PostgreSQL retains timezone-aware equality; a frozen NOW for
# the mutation would violate the unchanged updated_at/epoch model invariant.
AUTHORITY_MUTATION_NOW = NOW + timedelta(microseconds=1)


_THREAD_ERRORS = frozenset({"AssertionError", "AuthorityDenied", "OperationalError",
                            "IntegrityError", "DBAPIError", "TimeoutError", "ValueError", "UnexpectedError"})
_THREAD_PHASES = frozenset({"waiting", "change", "staged", "commit", "committed",
                            "require", "denied", "allowed"})
_AUTHORITY_PROBE_PHASES = frozenset({"revoke_change", "revoke_staged", "revoke_commit", "revoke_committed",
    "dispatch_waiting", "dispatch_require", "dispatch_denied", "dispatch_allowed", "outcomes", "threads"})
_AUTHORITY_PROBE_ERRORS = _THREAD_ERRORS | {"ThreadTimeout", "UnexpectedOutcome"}


def _guarded_authority_thread(name, operation, failures):
    try:
        operation()
    except Exception as error:
        # Never leave a background exception for PytestUnhandledThreadException:
        # that warning loses phase attribution and may print SQL/parameters.
        kind = type(error).__name__
        failures[name] = kind if kind in _THREAD_ERRORS else "UnexpectedError"


def _authority_diagnostics(phases, failures, outcomes):
    return {
        "phases": {name: value if value in _THREAD_PHASES else "unknown"
                   for name, value in phases.items() if name in {"revoke", "dispatch"}},
        "errors": {name: value if value in _THREAD_ERRORS else "UnexpectedError"
                   for name, value in failures.items() if name in {"revoke", "dispatch"}},
        "outcomes": sorted(value for value in outcomes if value in {"revoked", "denied", "allowed"}),
    }


def _authority_failure_probe(phases, failures, outcomes, *, threads_alive=False):
    """Strict fixed-enum JSON sentinel; runner must reject any other shape."""
    if threads_alive:
        phase, error = "threads", "ThreadTimeout"
    elif failures:
        name = "revoke" if "revoke" in failures else "dispatch"
        candidate = name + "_" + str(phases.get(name, ""))
        phase = candidate if candidate in _AUTHORITY_PROBE_PHASES else "outcomes"
        candidate_error = failures.get(name)
        error = candidate_error if candidate_error in _THREAD_ERRORS else "UnexpectedError"
    elif sorted(outcomes) != ["denied", "revoked"]:
        phase, error = "outcomes", "UnexpectedOutcome"
    else:
        return None
    return {"probe": "authority_concurrency", "status": "FAIL", "phase": phase, "error_code": error}


def safe_url(name):
    value = os.getenv(name)
    if not value:
        pytest.skip(f"CONDITIONAL: {name} is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db"} or (
        parsed.host == "postgres" and os.getenv("GITHUB_ACTIONS") == "true"
    )
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
    phases, failures, threads = {}, {}, []
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            seed_authority(db)
        resolver = AuthorityResolver(clock=lambda: AUTHORITY_MUTATION_NOW)

        def revoke():
            phases["revoke"] = "change"
            with sessions.begin() as db:
                resolver.change(
                    db, scope=scope(), principal_id=3, membership_role="viewer",
                    permissions=["metadata"], state="revoked", expected_epoch=1,
                )
                phases["revoke"] = "staged"
                changed.set()
                assert contender_started.wait(5)
                phases["revoke"] = "commit"
            phases["revoke"] = "committed"
            outcomes.append("revoked")

        def dispatch_check():
            phases["dispatch"] = "waiting"
            assert changed.wait(5)
            contender_started.set()
            try:
                with sessions.begin() as db:
                    phases["dispatch"] = "require"
                    resolver.require(db, scope(3), "action.approve", AUTHORITY_MUTATION_NOW, lock=True)
            except AuthorityDenied:
                phases["dispatch"] = "denied"
                outcomes.append("denied")
            else:
                phases["dispatch"] = "allowed"
                outcomes.append("allowed")

        first = Thread(target=_guarded_authority_thread, args=("revoke", revoke, failures))
        second = Thread(target=_guarded_authority_thread, args=("dispatch", dispatch_check, failures))
        threads = [first, second]
        first.start()
        second.start()
        first.join(10)
        second.join(10)
        diagnostic = _authority_diagnostics(phases, failures, outcomes)
        probe = _authority_failure_probe(phases, failures, outcomes,
                                          threads_alive=first.is_alive() or second.is_alive())
        if probe is not None:
            print(json.dumps(probe, sort_keys=True, separators=(",", ":")))
        assert not first.is_alive() and not second.is_alive(), diagnostic
        # Separate source lines give the safe CI frame protocol useful phase
        # attribution even when full assertion details are deliberately withheld.
        assert "revoke" not in failures, diagnostic
        assert "dispatch" not in failures, diagnostic
        assert sorted(outcomes) == ["denied", "revoked"], diagnostic
    finally:
        # Preserve the original 5s event / 10s pass-fail joins above. These waits
        # are teardown only, after a failure has already been decided; they can
        # never promote an original timeout to PASS. Release synthetic barriers,
        # then wait beyond each fixture's bounded 15s database statement timeout.
        changed.set()
        contender_started.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(20)
        engine.dispose()
        try:
            assert not any(thread.is_alive() for thread in threads), "authority_cleanup_threads_alive"
            with admin.begin() as db:
                db.execute(text("SET LOCAL lock_timeout='8000ms'"))
                db.execute(text("SET LOCAL statement_timeout='15000ms'"))
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
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
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            assert "v54_authority_states" in inspect(db).get_table_names()
        command.downgrade(cfg, "a54f001c0a01")
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()
