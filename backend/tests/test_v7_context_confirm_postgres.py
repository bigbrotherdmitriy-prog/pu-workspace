"""Opt-in migrated PostgreSQL contention, not a SQLite concurrency claim."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api import ai_secretary as ai
from app.models.ai_secretary import Message
from app.models.audit_log import AuditLog
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.response_draft import ResponseDraft
from app.models.user import User
from app.models.v54_provider_action import ProviderAction
from app.schema import CURRENT_SCHEMA_REVISION
from test_v7_context_confirm_recovery import _counts


def _owned_url(value):
    try:
        url = make_url(value)
    except Exception:
        raise ValueError("owned_mvp2_postgres_required") from None
    hosts = {"localhost", "127.0.0.1", "::1", "db"}
    if os.getenv("GITHUB_ACTIONS") == "true":
        hosts.add("postgres")
    if (url.get_backend_name() != "postgresql" or url.host not in hosts or url.query
            or re.fullmatch(r"puw_mvp2_test_[a-z0-9_]+", url.database or "") is None):
        raise ValueError("owned_mvp2_postgres_required")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


@pytest.fixture
def pg_context(monkeypatch):
    value = os.getenv("PUW_MVP2_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: owned MVP2 PostgreSQL not configured")
    url = _owned_url(value)
    schema = "context_confirm_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    engine = None
    created = False
    try:
        with admin.begin() as db:
            assert db.scalar(text("SELECT current_database()")) == url.database
            assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        isolated = url.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
        backend = Path(__file__).resolve().parents[1]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "migrations"))
        config.set_main_option("sqlalchemy.url", isolated.render_as_string(hide_password=False).replace("%", "%%"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        engine = create_engine(isolated, hide_parameters=True, connect_args={"connect_timeout": 5})
        sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
        with sessions.begin() as db:
            assert list(db.scalars(text("SELECT version_num FROM alembic_version"))) == ["a54f001c0a20"]
            org = Organization(name="Owned context test")
            user = User(name="Context manager", email="context-manager@example.test", is_admin=False)
            db.add_all([org, user])
            db.flush()
            project = Project(name="Context project", organization_id=org.id)
            db.add(project)
            db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
            message = Message(organization_id=org.id, project_id=project.id, created_by_user_id=user.id,
                source_type="email", source_external_id="pg-context", source_name="Test request",
                content="Просим подготовить отчёт до 15.10.2026. Риск критичной просрочки поставки. Требуется решение согласовать вариант.",
                summary="Pending", context_evidence="Ambiguous", context_confirmed=False, analysis_required=True)
            db.add(message)
            db.flush()
            ids = dict(message_id=message.id, project_id=project.id, user_id=user.id)
        monkeypatch.setattr(ai, "configured_action_adapter", lambda *_: SimpleNamespace(provider="synthetic"))
        yield engine, sessions, ids
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            assert re.fullmatch(r"context_confirm_test_[0-9a-f]{32}", schema)
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("scenario", ["duplicate_single", "bulk_vs_single", "failure_then_waiter"])
def test_pg_context_confirmation_serializes_real_engines(pg_context, monkeypatch, scenario):
    engine, sessions, ids = pg_context
    paused, release, started = Event(), Event(), Event()
    pids = []
    real_tasks = ai.create_tasks_from_files
    def pause_after_engine_commit(*args, **kwargs):
        result = real_tasks(*args, **kwargs)
        if not paused.is_set():
            paused.set()
            assert release.wait(7), "observer did not release first confirmation"
            if scenario == "failure_then_waiter":
                raise RuntimeError("injected first analysis failure")
        return result
    monkeypatch.setattr(ai, "create_tasks_from_files", pause_after_engine_commit)

    def confirm(first):
        with sessions() as db:
            user = db.get(User, ids["user_id"])
            # Force a stale identity-map row in the waiting request.
            cached = db.get(Message, ids["message_id"])
            if not first:
                pids.append(db.scalar(text("SELECT pg_backend_pid()")))
                started.set()
            try:
                if not first and scenario == "bulk_vs_single":
                    ai.confirm_context_bulk(ai.BulkContextConfirmation(
                        message_ids=[ids["message_id"]], project_id=ids["project_id"]), db, user)
                else:
                    ai.confirm_context(ids["message_id"], ai.ContextConfirmation(project_id=ids["project_id"]), db, user)
                assert not cached.analysis_required
                return "complete"
            except RuntimeError:
                db.rollback()
                if first and scenario == "failure_then_waiter":
                    return "rolled_back"
                raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm, True)
        try:
            assert paused.wait(5), "first confirmation did not reach real task engine commit"
            second = pool.submit(confirm, False)
            assert started.wait(3)
            deadline = monotonic() + 3
            blocked = False
            with engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pids[0]}):
                        blocked = True
                        break
                    sleep(.02)
                assert blocked, "waiting confirmation did not block on the PostgreSQL row lock"
                assert observer.scalar(select(func.count()).select_from(Task)) == 0
                assert observer.scalar(select(Message.context_confirmed).where(Message.id == ids["message_id"])) is False
        finally:
            release.set()
        assert first.result(timeout=8) == ("rolled_back" if scenario == "failure_then_waiter" else "complete")
        assert second.result(timeout=8) == "complete"
    with sessions() as db:
        assert _counts(db) == (2, 2, 1, 1, 1)
        assert all(task.message_id == ids["message_id"] and task.external_action_status == "proposed"
                   for task in db.scalars(select(Task)))
        assert set(db.scalars(select(ResponseDraft.message_id))) == {ids["message_id"]}
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "message_analysis_materialized")) == 1
        assert db.scalar(select(func.count()).select_from(ProviderAction)) == 0


@pytest.mark.parametrize("url", ["postgresql://u:p@example.com/puw_mvp2_test_x",
    "postgresql://u:p@localhost/production", "postgresql://u:p@localhost/puw_mvp2_test_x?options=-csearch_path=public",
    "sqlite:///test.db", "not-a-url"])
def test_context_pg_fixture_rejects_unowned_urls(url):
    with pytest.raises(ValueError, match="owned_mvp2_postgres_required"):
        _owned_url(url)
