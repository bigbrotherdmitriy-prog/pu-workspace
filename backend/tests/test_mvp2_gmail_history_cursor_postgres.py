"""Conditional real-PostgreSQL CAS proof for Gmail history checkpoints."""

import os
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.gmail_history import (
    GmailHistoryBusy,
    ensure_history_checkpoint,
    run_history_sync,
)
from app.models.job import BackgroundJob
from app.models.mailbox_identity import MailboxCutoverFlags
from app.models.user import User
from app.schema import CURRENT_SCHEMA_REVISION
from test_mvp2_gmail_history_cursor import HistoryService
from test_v54_mailbox_identity import enable_rollout, world


def _url():
    value = os.getenv("PUW_MVP2_GMAIL_HISTORY_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: Gmail history PostgreSQL URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_mvp2_test_") and not parsed.query
    return value


def test_postgresql_concurrent_checkpoint_claim_has_one_winner():
    engine = create_engine(_url(), hide_parameters=True, connect_args={"connect_timeout": 5})
    claimed = Event()
    release = Event()
    first_result = []
    first_error = []
    try:
        with Session(engine) as setup:
            assert setup.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION

            def user_factory(**overrides):
                user = User(
                    name="Synthetic PG history actor",
                    email=f"history-{uuid4().hex}@example.test",
                    is_admin=False,
                    **overrides,
                )
                setup.add(user)
                setup.flush()
                return user

            value = world(setup, user_factory)
            flags = setup.scalar(select(MailboxCutoverFlags).where(
                MailboxCutoverFlags.mail_connection_id == value.mail.id,
            ))
            enable_rollout(flags, "pilot_write")
            setup.commit()
            checkpoint = ensure_history_checkpoint(
                setup, project_id=value.project.id, actor=value.user,
                initial_history_id="100",
            )
            jobs = []
            for key in ("winner", "loser"):
                job = BackgroundJob(
                    kind="gmail.history.sync", payload={"checkpoint_id": checkpoint.id},
                    idempotency_key=f"pg-history-{key}-{uuid4().hex}", status="running",
                    priority=100, attempts=1, max_attempts=3, progress=1,
                )
                setup.add(job)
                jobs.append(job)
            setup.commit()
            first_job_id, second_job_id = jobs[0].id, jobs[1].id
            checkpoint_id = checkpoint.id

        def winner():
            try:
                with Session(engine) as session:
                    result = run_history_sync(
                        session, checkpoint_id=checkpoint_id, job_id=first_job_id,
                        service=HistoryService({None: {"history": [], "historyId": "101"}}),
                        process_refs=lambda _refs: (
                            claimed.set(), release.wait(10), {"processed": 0}
                        )[-1],
                        full_resync=lambda: ("200", {"processed": 0}),
                    )
                    first_result.append(result)
            except Exception as exc:  # assertion reports only the safe class
                first_error.append(exc.__class__.__name__)

        thread = Thread(target=winner)
        thread.start()
        assert claimed.wait(10)
        with Session(engine) as second:
            with pytest.raises(GmailHistoryBusy):
                run_history_sync(
                    second, checkpoint_id=checkpoint_id, job_id=second_job_id,
                    service=HistoryService({None: {"history": [], "historyId": "102"}}),
                    process_refs=lambda _refs: {"processed": 0},
                    full_resync=lambda: ("200", {"processed": 0}),
                )
        release.set()
        thread.join(10)
        assert not thread.is_alive() and first_error == []
        assert first_result == [{"processed": 0, "mode": "incremental"}]
    finally:
        release.set()
        engine.dispose()
