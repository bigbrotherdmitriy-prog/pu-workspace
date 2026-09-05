"""Synthetic scope and bounded-query acceptance for the MVP3 management centre."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.governance import DecisionUpdate, RiskUpdate, decisions, risks, update_decision, update_risk
from app.api.management import obligations
from app.api.organizations_contracts import list_contracts
from app.api.project_contacts import list_contacts
from app.database import Base
from app.models.governance import Decision, Risk
from app.models.job import BackgroundJob
from app.models.management import Obligation
from app.models.management_digest import ManagementDigestPreference
from app.models.organization_contract import Contract
from app.models.project_contact import ProjectContact
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_provider_action import ProviderAction
from app.mvp3.attention import attention_page
from app.mvp3.meeting_digest import schedule_digest_jobs
from app.mvp3.search import SearchFilters, project_search
from v54_pilot_fixture import pin, seed, uid


@contextmanager
def select_counter(db: Session):
    statements: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)


@pytest.fixture()
def scoped_world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)
        db.add_all([
            User(id=4, name="Synthetic viewer", email="viewer@example.test", is_admin=False),
            User(id=5, name="Synthetic manager", email="manager@example.test", is_admin=False),
            ProjectMember(project_id=4, user_id=2, role="manager"),
            ProjectMember(project_id=4, user_id=3, role="editor"),
            ProjectMember(project_id=4, user_id=4, role="viewer"),
            ProjectMember(project_id=4, user_id=5, role="manager"),
        ])
        db.flush()
        yield db
        db.rollback()
    engine.dispose()


def _obligation(index: int, *, project_id: int = 4, pins: list | None = None) -> Obligation:
    return Obligation(
        project_id=project_id, owner_user_id=2, title=f"Synthetic obligation {index}",
        status="confirmed", due_date=date(2026, 10, 1), due_time=time(12),
        timezone="Europe/Moscow", source_type="synthetic", source_id=f"o-{project_id}-{index}",
        source_name=f"synthetic-{index}", source_excerpt="synthetic only",
        source_hash=f"{project_id:02x}{index:062x}", confidence=1.0,
        evidence_pins=pins or [], review_state="verified",
    )


def _risk(index: int, *, project_id: int = 4) -> Risk:
    return Risk(
        project_id=project_id, owner_user_id=2, title=f"Synthetic risk {index}",
        description="synthetic only", criticality="medium", status="confirmed",
        source_type="synthetic", source_id=f"r-{project_id}-{index}",
        source_name=f"synthetic-{index}", source_excerpt="synthetic only",
        source_hash=f"{project_id:02x}{index + 2000:062x}", confidence=1.0,
        evidence_pins=[], review_state="verified",
    )


def _decision(index: int, *, project_id: int = 4) -> Decision:
    return Decision(
        project_id=project_id, initiator_user_id=2, owner_user_id=2,
        question=f"Synthetic decision {index}", status="confirmed",
        source_type="synthetic", source_id=f"d-{project_id}-{index}",
        source_name=f"synthetic-{index}", source_excerpt="synthetic only",
        source_hash=f"{project_id:02x}{index + 4000:062x}", confidence=1.0,
        evidence_pins=[], review_state="verified",
    )


def test_1000_record_pages_are_scoped_bounded_and_role_safe(scoped_world):
    db = scoped_world
    db.add_all([_obligation(i) for i in range(1000)])
    db.add_all([_risk(i) for i in range(250)])
    db.add_all([_decision(i) for i in range(250)])
    db.add_all([Contract(project_id=4, number=f"S-{i}", title=f"Synthetic contract {i}")
                for i in range(250)])
    db.add_all([ProjectContact(
        organization_id=1, project_id=4, created_by_user_id=2,
        name=f"Synthetic contact {i}", email=f"contact-{i}@example.test",
        normalized_email=f"contact-{i}@example.test", normalized_domain="example.test",
        active=True, confirmed=True, source="synthetic", resolution_state="confirmed",
        resolution_reason_code="synthetic",
    ) for i in range(250)])
    db.add_all([_obligation(1, project_id=9), _risk(1, project_id=9), _decision(1, project_id=9)])
    db.commit()

    viewer = db.get(User, 4)
    for call, key, maximum in (
        (lambda: obligations(4, db, viewer), "obligations", 3),
        (lambda: risks(4, db, viewer), "risks", 3),
        (lambda: decisions(4, db, viewer), "decisions", 3),
        (lambda: list_contacts(4, db, viewer), "contacts", 3),
        (lambda: list_contracts(4, db, viewer), "contracts", 14),
    ):
        with select_counter(db) as statements:
            response = call()
        assert len(response[key]) == 100
        assert response["count"] >= len(response[key])
        assert response["has_more"] is True
        assert len(statements) <= maximum
        assert all(item.get("project_id", 4) == 4 for item in response[key])

    with pytest.raises(HTTPException) as invalid:
        obligations(4, db, viewer, limit=201)
    assert invalid.value.status_code == 422
    with pytest.raises(HTTPException) as forbidden:
        update_risk(db.scalar(select(Risk.id).where(Risk.project_id == 4)),
                    RiskUpdate(status="dismissed"), db, viewer)
    assert forbidden.value.status_code == 403

    editor = db.get(User, 3)
    with pytest.raises(HTTPException) as manager_only:
        update_decision(db.scalar(select(Decision.id).where(Decision.project_id == 4)),
                        DecisionUpdate(status="dismissed"), db, editor)
    assert manager_only.value.status_code == 403
    manager = db.get(User, 5)
    changed = update_risk(db.scalar(select(Risk.id).where(Risk.project_id == 4)),
                          RiskUpdate(status="dismissed"), db, manager)
    assert changed["status"] == "dismissed"
    assert db.scalars(select(ProviderAction)).all() == []


def test_attention_and_search_do_not_leak_cross_project_evidence_or_scale_as_n_plus_one(scoped_world):
    db = scoped_world
    valid = pin("evidence", uid(16), tenant=1)
    unsafe = {**valid, "provider_payload": "must-not-leak"}
    db.add(_obligation(0, pins=[valid, unsafe]))
    db.add_all([_obligation(i + 1) for i in range(1000)])
    db.add_all([_risk(i) for i in range(40)] + [_decision(i) for i in range(40)])
    db.commit()

    with select_counter(db) as statements:
        attention = attention_page(db, project_id=4, limit=100)
    assert attention["scan_truncated"] is True
    assert attention["total"] == 1080
    assert len(attention["items"]) == 100
    assert len(statements) <= 6
    assert all("LIMIT" in statement.upper() for statement in statements[:4])
    all_pins = [pin_value for item in attention["items"] for pin_value in item["evidence_pins"]]
    assert all(set(pin_value) == {"ref", "version_kind", "value"} for pin_value in all_pins)
    assert "provider_payload" not in str(attention)

    with select_counter(db) as statements:
        found = project_search(
            db, organization_id=1, project_id=4, actor_user_id=4,
            filters=SearchFilters(types=("obligation", "risk", "decision")), limit=50,
        )
    assert len(found["items"]) == 50
    assert found["scan_truncated"] is True
    assert len(statements) <= 10
    assert all(item["project"]["id"] == 4 for item in found["items"])
    assert "provider_payload" not in str(found)
    assert found["external_actions_created"] is False


def test_digest_replay_for_1000_preferences_has_constant_select_count(scoped_world):
    db = scoped_world
    users = [User(id=100 + i, name=f"Digest {i}", email=f"digest-{i}@example.test", is_admin=False)
             for i in range(1000)]
    db.add_all(users)
    db.flush()
    db.add_all([ProjectMember(project_id=4, user_id=user.id, role="viewer") for user in users])
    db.add_all([ManagementDigestPreference(
        project_id=4, user_id=user.id, timezone="Europe/Moscow",
        quiet_start=time(20), quiet_end=time(8), channel="in_app", cadence="daily",
    ) for user in users])
    db.flush()
    preferences = db.scalars(select(ManagementDigestPreference).order_by(ManagementDigestPreference.id)).all()
    local_date = date(2026, 9, 7)
    db.add_all([BackgroundJob(
        kind="mvp3.management_digest", payload={}, status="completed",
        idempotency_key=f"mvp3.digest.preference:{row.id}:v{row.record_version}:{local_date.isoformat()}",
    ) for row in preferences])
    db.commit()

    with select_counter(db) as statements:
        scheduled = schedule_digest_jobs(db, now=datetime(2026, 9, 7, 10, tzinfo=timezone.utc))
    assert scheduled == 0
    assert len(statements) == 2
    assert db.scalars(select(ProviderAction)).all() == []
