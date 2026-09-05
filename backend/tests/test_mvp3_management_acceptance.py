"""Synthetic acceptance for the complete MVP3 management chain.

The corpus deliberately uses immutable synthetic Evidence.  It never invokes a
provider adapter and it asserts that durable job payloads contain identifiers
and scheduling facts only.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.organizations_contracts import (
    ContractCreate,
    ContractLinkUpdate,
    create_contract,
    update_contract_links,
)
from app.api.project_contacts import (
    ContactResolutionCommand,
    discover_contact_from_message,
    resolve_contact,
)
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.governance import Decision, GovernanceHistory, Risk
from app.models.job import BackgroundJob
from app.models.management import Meeting, Notification, Obligation, ObligationHistory
from app.models.management_digest import ManagementDigestPreference, ManagementProposalOrigin
from app.models.organization_contract import Contract, ContractVersion
from app.models.project_contact import ProjectContactHistory
from app.models.project_member import ProjectMember
from app.models.search import SavedSearchViewHistory
from app.models.task import Task
from app.models.user import User
from app.models.v54_provider_action import ProviderAction
from app.models.v54_pilot import EvidenceAssessment, SourceReference
from app.mvp3.attention import attention_page
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied, ManagementLifecycle
from app.mvp3.meeting_digest import (
    DigestPreference,
    DigestPreferenceService,
    MeetingActionCandidate,
    MeetingProposalService,
    install_digest_runtime,
    run_digest_job,
    schedule_digest_jobs,
)
from app.mvp3.search import (
    SearchDenied,
    SearchFilters,
    SearchValidationError,
    create_saved_view,
    get_saved_view_history,
    project_search,
    update_saved_view,
)
from v54_pilot_fixture import pin, seed, uid


@pytest.fixture()
def world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)
        db.get(app.models.Message, 6).context_confirmed = True
        source = db.get(SourceReference, uid(13))
        source.availability = "available"
        source.freshness = "fresh"
        source.sync_state = "current"
        db.add_all([
            ProjectMember(project_id=4, user_id=2, role="manager"),
            ProjectMember(project_id=4, user_id=3, role="editor"),
            Meeting(
                id=40,
                project_id=4,
                created_by_user_id=2,
                title="Synthetic acceptance meeting",
                status="completed",
            ),
        ])
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def evidence_pin() -> dict:
    return pin("evidence", uid(16), tenant=1)


def preference() -> DigestPreference:
    return DigestPreference(
        timezone="Europe/Moscow",
        quiet_start=time(20),
        quiet_end=time(8),
        channel="in_app",
        cadence="daily",
    )


def task_candidate() -> MeetingActionCandidate:
    return MeetingActionCandidate(
        kind="task",
        title="Передать синтетический комплект",
        owner_user_id=2,
        evidence_pins=[evidence_pin()],
        due_date=date(2026, 9, 1),
        timezone="Europe/Moscow",
    )


def test_full_management_chain_is_reviewable_replay_safe_and_content_free(world):
    proposals = MeetingProposalService()
    lifecycle = ManagementLifecycle()

    proposed = proposals.propose_message(
        world,
        project_id=4,
        message_id=6,
        actor_user_id=3,
        candidates=[task_candidate()],
    )[0]
    obligation = world.get(Obligation, proposed["entity_id"])
    assert obligation.status == "needs_confirmation"
    assert obligation.review_state == "needs_review"
    assert proposed["task_id"] is None
    assert world.scalars(select(Task)).all() == []

    # Extraction replay points to the same durable proposal and does not create
    # another obligation or origin link.
    replay = proposals.propose_message(
        world,
        project_id=4,
        message_id=6,
        actor_user_id=3,
        candidates=[task_candidate()],
    )[0]
    assert replay["entity_id"] == proposed["entity_id"]
    assert len(world.scalars(select(Obligation)).all()) == 1
    assert len(world.scalars(select(ManagementProposalOrigin)).all()) == 1

    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        proposals.confirm(
            world,
            project_id=4,
            actor_user_id=3,
            entity_type="obligation",
            entity_id=obligation.id,
            expected_version=1,
            create_internal_task=True,
        )
    assert world.scalars(select(Task)).all() == []

    confirmed = proposals.confirm(
        world,
        project_id=4,
        actor_user_id=2,
        entity_type="obligation",
        entity_id=obligation.id,
        expected_version=1,
        create_internal_task=True,
    )
    task = world.get(Task, confirmed["task_id"])
    assert task.external_action_status == "proposed"
    assert task.google_task_id is None and task.google_calendar_event_id is None

    # Repeating the exact confirmation is an idempotent read of the same task.
    confirmed_again = proposals.confirm(
        world,
        project_id=4,
        actor_user_id=2,
        entity_type="obligation",
        entity_id=obligation.id,
        expected_version=3,
        create_internal_task=True,
    )
    assert confirmed_again["task_id"] == task.id
    assert len(world.scalars(select(Task)).all()) == 1

    scope = lifecycle.scope(world, project_id=4, actor_user_id=2)
    risk = lifecycle.create_risk(
        world,
        scope=scope,
        title="Риск просрочки",
        owner_user_id=2,
        evidence_pins=[evidence_pin()],
        criticality="critical",
        obligation_id=obligation.id,
        task_id=task.id,
    )
    decision = lifecycle.create_decision(
        world,
        scope=scope,
        question="Утвердить корректирующий план?",
        owner_user_id=2,
        evidence_pins=[evidence_pin()],
        obligation_id=obligation.id,
        task_id=task.id,
        risk_id=risk.id,
    )
    risk = lifecycle.transition_governance(
        world,
        scope=scope,
        entity_type="risk",
        entity_id=risk.id,
        expected_version=1,
        status="confirmed",
    )
    decision = lifecycle.transition_governance(
        world,
        scope=scope,
        entity_type="decision",
        entity_id=decision.id,
        expected_version=1,
        status="confirmed",
    )
    assert (risk.obligation_id, risk.task_id, decision.risk_id) == (
        obligation.id,
        task.id,
        risk.id,
    )

    attention = attention_page(
        world,
        project_id=4,
        now=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        limit=100,
    )
    items = {(item["entity_type"], item["entity_id"]): item for item in attention["items"]}
    assert items[("obligation", obligation.id)]["explanation"] == "deadline_passed"
    assert items[("task", task.id)]["priority"] == "critical"
    assert items[("risk", risk.id)]["evidence_pins"] == [evidence_pin()]
    assert items[("decision", decision.id)]["status"] == "confirmed"
    assert attention["external_actions_created"] is False

    pref = DigestPreferenceService().put(
        world,
        project_id=4,
        user_id=2,
        expected_version=0,
        preference=preference(),
    )
    world.commit()
    now = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
    assert schedule_digest_jobs(world, now=now) == 1
    assert schedule_digest_jobs(world, now=now) == 0
    job = world.scalar(select(BackgroundJob).where(BackgroundJob.kind == "mvp3.management_digest"))
    assert job.payload == {
        "project_id": 4,
        "user_id": 2,
        "local_date": "2026-09-07",
        "preference_id": pref.id,
        "preference_version": 1,
    }
    forbidden = {
        "content", "minutes", "message", "document", "email", "evidence_pins",
        "source_excerpt", "payload", "token",
    }
    assert not forbidden.intersection(job.payload)

    try:
        install_digest_runtime(lambda: world, clock=lambda: now)
        result = run_digest_job(job.payload)
    finally:
        install_digest_runtime()
    assert result["status"] == "created"
    assert result["external_actions_created"] is False
    assert world.scalar(select(Notification).where(
        Notification.kind == "management_digest",
    )) is not None
    assert world.scalars(select(ProviderAction)).all() == []


def test_contract_contact_search_and_saved_view_preserve_cas_and_history(world):
    owner = world.get(User, 2)
    created = create_contract(
        4,
        ContractCreate(
            number="M3-ACCEPT-1",
            title="Синтетический договор",
            counterparty="ООО Синтетика",
            contract_kind="prime_reference",
            status="draft",
            signed_at=date(2026, 8, 20),
        ),
        world,
        owner,
    )
    changed = update_contract_links(
        4,
        created["id"],
        ContractLinkUpdate(
            expected_record_version=1,
            title="Подписанный синтетический договор",
            status="active",
        ),
        world,
        owner,
    )
    replay = update_contract_links(
        4,
        created["id"],
        ContractLinkUpdate(
            expected_record_version=2,
            title="Подписанный синтетический договор",
            status="active",
        ),
        world,
        owner,
    )
    assert changed["record_version"] == replay["record_version"] == 2
    with pytest.raises(HTTPException) as stale_contract:
        update_contract_links(
            4,
            created["id"],
            ContractLinkUpdate(expected_record_version=1, title="Stale overwrite"),
            world,
            owner,
        )
    assert stale_contract.value.status_code == 409
    assert [row.event for row in world.scalars(select(ContractVersion).where(
        ContractVersion.contract_id == created["id"],
    ).order_by(ContractVersion.sequence))] == ["created", "updated"]

    contact = discover_contact_from_message(
        world,
        4,
        "Synthetic Client <client@synthetic.example>",
        "Synthetic acceptance context",
        owner,
        mail_connection_id=uid(11),
        source_message_id=6,
    )
    assert contact.resolution_state == "proposed" and contact.confirmed is False
    command = ContactResolutionCommand(
        decision_key="mvp3.accept.contact.1",
        expected_record_version=1,
        decision="correct",
        company="ООО Синтетика",
        email="corrected@synthetic.example",
        reason_code="reviewed_by_operator",
    )
    resolved = resolve_contact(contact.id, command, world, owner)
    assert resolved["confirmed"] is True and resolved["record_version"] == 2
    assert resolve_contact(contact.id, command, world, owner)["already_applied"] is True
    with pytest.raises(HTTPException) as stale_contact:
        resolve_contact(
            contact.id,
            command.model_copy(update={
                "decision_key": "mvp3.accept.contact.2",
                "expected_record_version": 1,
            }),
            world,
            owner,
        )
    assert stale_contact.value.status_code == 409
    assert len(world.scalars(select(ProjectContactHistory).where(
        ProjectContactHistory.contact_id == contact.id,
    )).all()) == 1

    found = project_search(
        world,
        organization_id=1,
        project_id=4,
        actor_user_id=2,
        filters=SearchFilters(
            types=("contract",),
            contract_id=created["id"],
            counterparty="синтетика",
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
        ),
    )
    assert [(item["entity_type"], item["entity_id"]) for item in found["items"]] == [
        ("contract", created["id"]),
    ]
    assert found["external_actions_created"] is False

    view = create_saved_view(
        world,
        organization_id=1,
        project_id=4,
        actor_user_id=2,
        name="Договоры контрагента",
        filters=SearchFilters(types=("contract",), counterparty="синтетика"),
    )
    updated = update_saved_view(
        world,
        organization_id=1,
        project_id=4,
        actor_user_id=2,
        view_id=view.id,
        expected_version=1,
        name="Активные договоры",
        filters=SearchFilters(types=("contract",)),
    )
    assert updated.record_version == 2
    with pytest.raises(SearchValidationError, match="version_conflict"):
        update_saved_view(
            world,
            organization_id=1,
            project_id=4,
            actor_user_id=2,
            view_id=view.id,
            expected_version=1,
            name="stale",
            filters=SearchFilters(),
        )
    history = get_saved_view_history(
        world,
        organization_id=1,
        project_id=4,
        actor_user_id=2,
        view_id=view.id,
    )
    assert [(row.sequence, row.event) for row in history] == [(1, "created"), (2, "updated")]
    assert len(world.scalars(select(SavedSearchViewHistory).where(
        SavedSearchViewHistory.view_id == view.id,
    )).all()) == 2

    audit_text = "\n".join(row.details or "" for row in world.scalars(select(AuditLog)).all())
    assert "corrected@synthetic.example" not in audit_text
    assert "Synthetic acceptance context" not in audit_text


def test_rbac_stale_versions_corrections_and_revoked_evidence_fail_closed(world):
    lifecycle = ManagementLifecycle()
    proposals = MeetingProposalService(lifecycle)
    proposed = proposals.propose_message(
        world,
        project_id=4,
        message_id=6,
        actor_user_id=3,
        candidates=[task_candidate()],
    )[0]
    obligation = world.get(Obligation, proposed["entity_id"])

    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        lifecycle.scope(world, project_id=9, actor_user_id=2)
    with pytest.raises(SearchDenied, match="scope_unavailable"):
        project_search(
            world,
            organization_id=2,
            project_id=9,
            actor_user_id=2,
            filters=SearchFilters(),
        )
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        proposals.confirm(
            world,
            project_id=4,
            actor_user_id=3,
            entity_type="obligation",
            entity_id=obligation.id,
            expected_version=1,
            create_internal_task=True,
        )

    confirmed = proposals.confirm(
        world,
        project_id=4,
        actor_user_id=2,
        entity_type="obligation",
        entity_id=obligation.id,
        expected_version=1,
        create_internal_task=True,
    )
    scope = lifecycle.scope(world, project_id=4, actor_user_id=2)
    with pytest.raises(ManagementConflict, match="version_conflict"):
        lifecycle.transition_obligation(
            world,
            scope=scope,
            obligation_id=obligation.id,
            expected_version=1,
            status="in_progress",
        )

    obligation = lifecycle.transition_obligation(
        world,
        scope=scope,
        obligation_id=obligation.id,
        expected_version=3,
        status="in_progress",
    )
    obligation = lifecycle.transition_obligation(
        world,
        scope=scope,
        obligation_id=obligation.id,
        expected_version=4,
        status="fulfilled",
        result_note="Synthetic completion evidence reviewed",
    )
    with pytest.raises(ManagementDenied, match="invalid_input"):
        lifecycle.transition_obligation(
            world,
            scope=scope,
            obligation_id=obligation.id,
            expected_version=5,
            status="in_progress",
        )
    corrected = lifecycle.transition_obligation(
        world,
        scope=scope,
        obligation_id=obligation.id,
        expected_version=5,
        status="in_progress",
        reason="Operator corrected premature completion",
    )
    assert corrected.record_version == 6
    assert [(row.sequence, row.resulting_version) for row in world.scalars(
        select(ObligationHistory).where(
            ObligationHistory.obligation_id == obligation.id,
        ).order_by(ObligationHistory.sequence)
    )] == [(1, 1), (2, 2), (3, 4), (4, 5), (5, 6)]

    # Message extraction is still proposal-only and re-resolves exact evidence.
    message = seed_message = world.get(app.models.Message, 6)
    seed_message.context_confirmed = True
    decision = proposals.propose_message(
        world,
        project_id=4,
        message_id=message.id,
        actor_user_id=3,
        candidates=[MeetingActionCandidate(
            kind="decision",
            title="Принять синтетическое решение?",
            owner_user_id=2,
            evidence_pins=[evidence_pin()],
        )],
    )[0]
    assert world.get(Decision, decision["entity_id"]).status == "needs_confirmation"
    assert world.scalars(select(ProviderAction)).all() == []

    world.get(EvidenceAssessment, uid(16)).freshness = "stale"
    world.flush()
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        proposals.list_for_origin(
            world,
            project_id=4,
            actor_user_id=2,
            origin_type="message",
            origin_id=message.id,
        )
    assert world.get(Decision, decision["entity_id"]).status == "needs_confirmation"
    assert confirmed["task_id"] == world.get(Obligation, obligation.id).task_id

    # Histories exist for every accepted state transition and remain append-only
    # under their dedicated model guards.
    assert world.scalars(select(GovernanceHistory)).all()
    history = world.scalar(select(ObligationHistory).where(
        ObligationHistory.obligation_id == obligation.id,
    ))
    history.reason = "tampered"
    with pytest.raises(ValueError, match="management_history_is_append_only"):
        world.flush()
