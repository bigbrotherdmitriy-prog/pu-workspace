"""Opt-in real PostgreSQL CAS proof; never run against a production URL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.mailbox_identity.authority import renew_project_mailbox_authority
from app.mailbox_identity.service import MailboxConflict
from app.models.audit_log import AuditLog
from app.models.mailbox_identity import MailboxAuthorityState
from app.models.user import User
from test_v54_mailbox_rollout_controls import rollout_world


def test_concurrent_renewals_have_one_winner_and_one_audit():
    if os.getenv("PU_TEST_POSTGRES") != "1":
        pytest.skip("CONDITIONAL: isolated PostgreSQL required for real CAS proof")
    url = make_url(os.environ["DATABASE_URL"])
    assert url.get_backend_name() == "postgresql" and url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database == "pu_workspace_test" and not url.query
    # Match the application's installed psycopg 3 driver, not SQLAlchemy's
    # legacy psycopg2 default for a bare postgresql:// CI URL.
    url = url.set(drivername="postgresql+psycopg")
    schema = "mailbox_renew_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    engine = create_engine(url, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def isolate(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
            cursor.execute("SET statement_timeout TO '15s'")
        connection.commit()

    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            def factory(**kw):
                user = User(name="Synthetic owner", **kw)
                db.add(user); db.flush()
                return user
            w = rollout_world(db, factory)
            w.authority.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
            actor_id = w.actor.id
            args = dict(organization_id=w.organization.id, project_id=w.project.id,
                mail_connection_id=w.connection.id, credential_generation=w.generation,
                binding_epoch=w.identity.binding_epoch,
                valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
                approval="CONFIRM", reason="owner_confirmed_project_reconnect")
            db.commit()
        barrier = Barrier(2)
        def contender():
            with Session(engine) as db:
                actor = db.get(User, actor_id)
                barrier.wait(timeout=5)
                try:
                    renew_project_mailbox_authority(db, args, actor=actor, expected_version=7)
                    db.commit()
                    return "won"
                except MailboxConflict:
                    db.rollback()
                    return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: contender(), range(2)))
        assert sorted(outcomes) == ["conflict", "won"]
        with Session(engine) as db:
            assert db.scalar(select(MailboxAuthorityState)).authority_version == 8
            assert len(list(db.scalars(select(AuditLog).where(
                AuditLog.action == "mailbox_authority_renewed")))) == 1
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
