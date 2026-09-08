"""Opt-in isolated local PostgreSQL race test; never a SQLite substitute."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.automation_engine import prepare_rule_run
from app.models.automation_rule import AutomationRule
from test_v7_automation_period_idempotency import counts, rule_id, run, seed


def _owned_database_url():
    value = os.getenv("PUW_V7_AUTOMATION_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: owned local automation PostgreSQL URL is not configured")
    try:
        parsed = make_url(value)
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        if os.getenv("GITHUB_ACTIONS") == "true":
            allowed_hosts.add("postgres")
        valid = (parsed.get_backend_name() == "postgresql" and parsed.host in allowed_hosts
            and (parsed.database or "").startswith(("puw_v7_test_", "puw_v54_test_"))
            and not parsed.query)
    except Exception:
        raise ValueError("unsafe_automation_test_database") from None
    if not valid:
        raise ValueError("unsafe_automation_test_database")
    return value


@pytest.mark.parametrize("host,ci", [("localhost", None), ("127.0.0.1", None), ("postgres", "true")])
def test_owned_database_guard_accepts_local_or_explicit_ci_service(monkeypatch, host, ci):
    value = f"postgresql://synthetic@{host}/puw_v7_test_automation_period"
    monkeypatch.setenv("PUW_V7_AUTOMATION_DATABASE_URL", value)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    if ci is not None:
        monkeypatch.setenv("GITHUB_ACTIONS", ci)
    assert _owned_database_url() == value


@pytest.mark.parametrize("value,ci", [
    ("postgresql://synthetic@postgres/puw_v7_test_automation_period", None),
    ("postgresql://synthetic@postgres/puw_v7_test_automation_period", "false"),
    ("postgresql://synthetic@remote.invalid/puw_v7_test_automation_period", "true"),
    ("postgresql://synthetic@localhost/production", "true"),
    ("postgresql://synthetic@localhost/puw_v7_test_automation_period?sslmode=require", "true"),
    ("not-a-url", None),
])
def test_owned_database_guard_rejects_unsafe_targets_without_dsn(monkeypatch, value, ci):
    monkeypatch.setenv("PUW_V7_AUTOMATION_DATABASE_URL", value)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    if ci is not None:
        monkeypatch.setenv("GITHUB_ACTIONS", ci)
    with pytest.raises(ValueError) as error:
        _owned_database_url()
    assert str(error.value) == "unsafe_automation_test_database"


def test_postgres_two_manual_days_serialize_to_one_period_pair():
    value = _owned_database_url()
    schema = "v7_automation_period_" + uuid4().hex
    admin = create_engine(value, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(value, hide_parameters=True, connect_args={
        "connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=12000",
    })
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        seed(sessions)
        identifier = rule_id(sessions)
        ready = Barrier(2, timeout=10)
        def prepare(day):
            with sessions() as db:
                rule = db.get(AutomationRule, identifier)
                ready.wait()
                row = prepare_rule_run(db, rule, date(2028, 2, day))
                return row.id, row.task_id, row.response_draft_id
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(prepare, day) for day in (10, 29)]
            results = [future.result(timeout=25) for future in futures]
        assert results[0] == results[1]
        assert counts(sessions) == (1, 1, 1)
        assert run(sessions, identifier, date(2028, 2, 21)) == results[0]
    finally:
        engine.dispose()
        # The exact random schema was created above by this test alone.
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
