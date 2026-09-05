"""Durable mailbox-scoped Gmail history checkpoint acceptance."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.gmail_history import (
    GmailHistoryBusy,
    GmailHistoryUnavailable,
    enqueue_history_sync,
    ensure_history_checkpoint,
    run_history_sync,
)
from app.models.job import BackgroundJob
from app.models.mailbox_identity import (
    GmailHistoryCheckpoint,
    GmailHistoryCheckpointEvent,
    MailboxCutoverFlags,
)
from test_v54_mailbox_identity import enable_rollout, world


class HttpError(RuntimeError):
    def __init__(self, status: int):
        super().__init__("private response")
        self.resp = SimpleNamespace(status=status, headers={})


class HistoryResource:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        value = self.pages[kwargs.get("pageToken")]
        def execute():
            if isinstance(value, BaseException):
                raise value
            return value
        return SimpleNamespace(execute=execute)


class UsersResource:
    def __init__(self, pages):
        self.history_resource = HistoryResource(pages)

    def history(self):
        return self.history_resource


class HistoryService:
    def __init__(self, pages):
        self.users_resource = UsersResource(pages)

    def users(self):
        return self.users_resource


def checkpoint_world(db_session, user_factory, *, history_id="100"):
    value = world(db_session, user_factory)
    flags = value.db.scalar(select(MailboxCutoverFlags))
    enable_rollout(flags, "pilot_write")
    value.db.commit()
    checkpoint = ensure_history_checkpoint(
        value.db,
        project_id=value.project.id,
        actor=value.user,
        initial_history_id=history_id,
    )
    return value, checkpoint


def test_checkpoint_and_job_are_mailbox_scoped_and_idempotent(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    assert checkpoint.organization_id == value.org.id
    assert checkpoint.identity_id == value.identity.id
    assert checkpoint.mail_connection_id == value.mail.id
    assert checkpoint.credential_generation == value.generation
    assert checkpoint.binding_epoch == value.identity.binding_epoch
    assert checkpoint.project_id == value.project.id
    assert checkpoint.status == "active" and checkpoint.checkpoint_epoch == 1

    first = enqueue_history_sync(value.db, checkpoint.id)
    second = enqueue_history_sync(value.db, checkpoint.id)
    assert first.id == second.id
    assert first.payload == {"checkpoint_id": checkpoint.id}
    assert value.db.scalar(select(BackgroundJob).where(
        BackgroundJob.kind == "gmail.history.sync",
    )) is first


def test_incremental_history_advances_once_and_deduplicates_refs(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    service = HistoryService({
        None: {
            "history": [{"messagesAdded": [{"message": {"id": "m-1"}}]}],
            "nextPageToken": "page-2",
        },
        "page-2": {
            "history": [
                {"messages": [{"id": "m-1"}, {"id": "m-2"}]},
                {"messagesAdded": [{"message": {"id": "m-2"}}]},
            ],
            "historyId": "105",
        },
    })
    observed = []
    result = run_history_sync(
        value.db,
        checkpoint_id=checkpoint.id,
        job_id=41,
        service=service,
        process_refs=lambda refs: observed.extend(refs) or {"processed": len(refs)},
        full_resync=lambda: pytest.fail("incremental cursor must not resync"),
    )
    value.db.refresh(checkpoint)
    assert observed == [{"id": "m-1"}, {"id": "m-2"}]
    assert result == {"processed": 2, "mode": "incremental"}
    assert checkpoint.last_history_id == "105"
    assert checkpoint.status == "active" and checkpoint.checkpoint_epoch == 3
    assert checkpoint.active_job_id is None
    assert [row.outcome_code for row in value.db.scalars(select(
        GmailHistoryCheckpointEvent
    ).order_by(GmailHistoryCheckpointEvent.from_epoch))] == [
        "sync_claimed", "sync_completed",
    ]
    assert service.users_resource.history_resource.calls == [
        {"userId": "me", "startHistoryId": "100", "maxResults": 100},
        {"userId": "me", "startHistoryId": "100", "maxResults": 100,
         "pageToken": "page-2"},
    ]


def test_expired_cursor_runs_bounded_resync_without_duplicate_effect(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    service = HistoryService({None: HttpError(404)})
    effects = []

    result = run_history_sync(
        value.db,
        checkpoint_id=checkpoint.id,
        job_id=42,
        service=service,
        process_refs=lambda refs: effects.extend(refs) or {"processed": len(refs)},
        full_resync=lambda: ("200", {"processed": 1, "deduplicated": True}),
    )
    value.db.refresh(checkpoint)
    assert result == {"processed": 1, "deduplicated": True, "mode": "resync"}
    assert effects == []
    assert checkpoint.last_history_id == "200" and checkpoint.status == "active"
    assert [row.outcome_code for row in value.db.scalars(select(
        GmailHistoryCheckpointEvent
    ).order_by(GmailHistoryCheckpointEvent.from_epoch))] == [
        "sync_claimed", "cursor_expired", "resync_completed",
    ]

    # Replaying the same durable job is a read-only terminal replay.
    replay = run_history_sync(
        value.db,
        checkpoint_id=checkpoint.id,
        job_id=42,
        service=service,
        process_refs=lambda refs: pytest.fail("terminal replay must not read messages"),
        full_resync=lambda: pytest.fail("terminal replay must not resync"),
    )
    assert replay == result


def test_concurrent_job_has_one_cas_winner_and_same_job_can_resume(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    service = HistoryService({None: {
        "history": [{"messages": [{"id": "m-recovered"}]}],
        "historyId": "101",
    }})
    checkpoint.status = "syncing"
    checkpoint.sync_mode = "incremental"
    checkpoint.active_job_id = 51
    checkpoint.checkpoint_epoch = 2
    value.db.commit()

    with pytest.raises(GmailHistoryBusy):
        run_history_sync(
            value.db, checkpoint_id=checkpoint.id, job_id=52, service=service,
            process_refs=lambda _refs: {}, full_resync=lambda: ("x", {}),
        )
    observed = []
    result = run_history_sync(
        value.db, checkpoint_id=checkpoint.id, job_id=51, service=service,
        process_refs=lambda refs: observed.extend(refs) or {"processed": 1},
        full_resync=lambda: ("x", {}),
    )
    assert result["processed"] == 1 and observed == [{"id": "m-recovered"}]


def test_generation_rotation_blocks_provider_read_and_checkpoint_advance(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    value.identity.credential_generation += 1
    value.identity.binding_epoch += 1
    value.db.commit()
    service = HistoryService({None: {"history": [], "historyId": "101"}})
    with pytest.raises(GmailHistoryUnavailable):
        run_history_sync(
            value.db, checkpoint_id=checkpoint.id, job_id=60, service=service,
            process_refs=lambda _refs: {}, full_resync=lambda: ("x", {}),
        )
    assert service.users_resource.history_resource.calls == []


def test_partial_ingest_never_advances_cursor_and_replay_deduplicates_successes(
        db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    pages = {None: {
        "history": [{"messages": [{"id": "m-ok"}, {"id": "m-failed"}]}],
        "historyId": "101",
    }}
    with pytest.raises(GmailHistoryUnavailable, match="history_ingest_incomplete"):
        run_history_sync(
            value.db, checkpoint_id=checkpoint.id, job_id=61,
            service=HistoryService(pages),
            process_refs=lambda _refs: {"processed": 1, "failed": 1},
            full_resync=lambda: ("x", {}),
        )
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id == "100" and checkpoint.status == "active"

    observed = []
    result = run_history_sync(
        value.db, checkpoint_id=checkpoint.id, job_id=61,
        service=HistoryService(pages),
        process_refs=lambda refs: observed.extend(refs) or {
            "processed": 1, "skipped": 1, "provider_response": 1,
        },
        full_resync=lambda: ("x", {}),
    )
    assert observed == [{"id": "m-ok"}, {"id": "m-failed"}]
    assert result == {"processed": 1, "skipped": 1, "mode": "incremental"}


def test_new_verified_generation_resets_cursor_to_bounded_resync(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    from app.mailbox_identity.service import MailboxIdentityService

    _identity, _mail, generation = MailboxIdentityService().bind_verified_google_subject(
        value.db,
        organization_id=value.org.id,
        google_token_id=value.token.id,
        subject=value.identity.account_key,
    )
    rotated_flags = value.db.scalar(select(MailboxCutoverFlags).where(
        MailboxCutoverFlags.credential_generation == generation,
    ))
    enable_rollout(rotated_flags, "pilot_write")
    value.db.commit()
    rotated = ensure_history_checkpoint(
        value.db, project_id=value.project.id, actor=value.user,
    )
    assert rotated.id == checkpoint.id
    assert rotated.credential_generation == generation
    assert rotated.status == "resync_required"
    assert rotated.last_history_id is None
    assert value.db.scalar(select(GmailHistoryCheckpointEvent.outcome_code).where(
        GmailHistoryCheckpointEvent.checkpoint_id == checkpoint.id,
        GmailHistoryCheckpointEvent.outcome_code == "generation_rotated",
    )) == "generation_rotated"


@pytest.mark.parametrize("token", ["", 123, "x" * 2001, "repeat"])
def test_malformed_or_repeated_page_token_never_advances_checkpoint(
        db_session, user_factory, token):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    pages = {None: {
        "history": [{"messages": [{"id": "m-1"}]}],
        "nextPageToken": token,
    }}
    if token == "repeat":
        pages[token] = {
            "history": [{"messages": [{"id": "m-2"}]}],
            "nextPageToken": token,
        }
    with pytest.raises(GmailHistoryUnavailable):
        run_history_sync(
            value.db, checkpoint_id=checkpoint.id, job_id=70, service=HistoryService(pages),
            process_refs=lambda _refs: pytest.fail("malformed history must not ingest"),
            full_resync=lambda: ("x", {}),
        )
    value.db.refresh(checkpoint)
    assert checkpoint.last_history_id == "100"
    assert checkpoint.status == "active"


def test_checkpoint_events_are_append_only_and_content_free(db_session, user_factory):
    value, checkpoint = checkpoint_world(db_session, user_factory)
    run_history_sync(
        value.db, checkpoint_id=checkpoint.id, job_id=80,
        service=HistoryService({None: {"history": [], "historyId": "101"}}),
        process_refs=lambda _refs: {"processed": 0}, full_resync=lambda: ("x", {}),
    )
    event = value.db.scalar(select(GmailHistoryCheckpointEvent))
    assert set(event.__table__.columns.keys()) == {
        "id", "organization_id", "checkpoint_id", "from_epoch", "to_epoch",
        "outcome_code", "job_id", "created_at",
    }
    event.outcome_code = "provider-id-must-not-be-written"
    with pytest.raises(ValueError, match="append_only_record"):
        value.db.flush()


def test_existing_worker_dispatches_only_opaque_checkpoint_payload(monkeypatch):
    from app import gmail_history
    from app.jobs import handlers

    seen = []
    monkeypatch.setattr(
        gmail_history,
        "run_gmail_history_job",
        lambda payload: seen.append(payload) or {"processed": 0},
    )
    payload = {"checkpoint_id": "00000000-0000-0000-0000-000000000001"}
    assert handlers.run("gmail.history.sync", payload) == {"processed": 0}
    assert seen == [payload]


def test_resync_pins_profile_before_listing_and_ingest():
    from app.gmail_history import bounded_history_resync
    observed = []
    def profile(**kwargs):
        observed.append("profile")
        return SimpleNamespace(execute=lambda: {"historyId": "201"})
    def listing(**kwargs):
        observed.append("listing")
        return SimpleNamespace(execute=lambda: {"messages": [{"id": "new"}]})
    users = SimpleNamespace(getProfile=profile, messages=lambda: SimpleNamespace(list=listing))
    service = SimpleNamespace(users=lambda: users)
    result = bounded_history_resync(
        service, lambda refs: observed.append("ingest") or {"processed": len(refs)}, lambda: None,
    )
    assert result == ("201", {"processed": 1})
    assert observed == ["profile", "listing", "ingest"]


def test_bounded_resync_rejects_unprocessed_next_page():
    from app.gmail_history import bounded_history_resync
    users = SimpleNamespace(
        getProfile=lambda **kwargs: SimpleNamespace(execute=lambda: {"historyId": "201"}),
        messages=lambda: SimpleNamespace(list=lambda **kwargs: SimpleNamespace(execute=lambda: {
            "messages": [{"id": str(i)} for i in range(100)], "nextPageToken": "more",
        })),
    )
    with pytest.raises(ValueError, match="gmail_resync_incomplete"):
        bounded_history_resync(SimpleNamespace(users=lambda: users),
            lambda refs: pytest.fail("partial listing must not ingest or advance"), lambda: None)


def test_worker_attempt_and_expired_lease_cannot_continue(db_session):
    from datetime import datetime, timedelta, timezone
    from app.gmail_history import require_history_execution_claim
    now = datetime.now(timezone.utc)
    job = BackgroundJob(kind="gmail.history.sync", payload={}, status="running",
        worker_id="worker-current", attempts=2, locked_at=now,
        lease_expires_at=now + timedelta(minutes=5))
    db_session.add(job)
    db_session.commit()
    require_history_execution_claim(db_session, (job.id, job.worker_id, 2, now))
    with pytest.raises(GmailHistoryBusy):
        require_history_execution_claim(db_session, (job.id, job.worker_id, 1, now))
    job.lease_expires_at = now - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(GmailHistoryBusy):
        require_history_execution_claim(db_session, (job.id, job.worker_id, 2, now))


def test_checkpoint_fence_is_refreshed_before_ingest(db_session, user_factory):
    from sqlalchemy import update
    value, checkpoint = checkpoint_world(db_session, user_factory)
    original_epoch = checkpoint.checkpoint_epoch
    class SupersededService:
        def users(self):
            def listing(**kwargs):
                def execute():
                    value.db.execute(update(GmailHistoryCheckpoint).where(
                        GmailHistoryCheckpoint.id == checkpoint.id,
                    ).values(checkpoint_epoch=original_epoch + 2).execution_options(synchronize_session=False))
                    value.db.commit()
                    return {"history": [], "historyId": "102"}
                return SimpleNamespace(execute=execute)
            return SimpleNamespace(history=lambda: SimpleNamespace(list=listing))
    with pytest.raises(GmailHistoryBusy):
        run_history_sync(value.db, checkpoint_id=checkpoint.id, job_id=90,
            service=SupersededService(),
            process_refs=lambda refs: pytest.fail("superseded reader must not ingest"),
            full_resync=lambda: pytest.fail("not a resync"))
    value.db.refresh(checkpoint)
    assert checkpoint.checkpoint_epoch == original_epoch + 2
    assert checkpoint.status == "syncing"
