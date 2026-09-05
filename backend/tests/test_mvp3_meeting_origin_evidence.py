"""Synthetic exact-origin denial until meetings have a durable protocol binding."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import governance, management
from app.models.audit_log import AuditLog
from app.models.governance import Decision
from app.models.management import Meeting, Obligation
from app.models.management_digest import ManagementProposalOrigin
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion
from app.mvp3.lifecycle import ManagementDenied, ManagementLifecycle
from app.mvp3.meeting_digest import MeetingProposalService
from test_mvp3_meeting_digest import candidate, evidence_pin, world  # noqa: F401
from v54_pilot_fixture import uid


@pytest.mark.parametrize("kind", ["obligation", "task", "decision"])
def test_meeting_cannot_claim_unrelated_same_project_message_evidence(world, kind):
    # This pin is valid current Evidence from a message attachment, not a meeting.
    with pytest.raises(ManagementDenied, match="invalid_meeting_source"):
        MeetingProposalService().propose(world, project_id=4, meeting_id=1,
            actor_user_id=2, candidates=[candidate(kind)])
    assert world.scalars(select(ManagementProposalOrigin)).all() == []
    assert world.scalars(select(Obligation)).all() == []
    assert world.scalars(select(Decision)).all() == []
    assert world.scalars(select(Task)).all() == []
    assert world.scalar(select(AuditLog).where(AuditLog.action == "mvp3_proposal_created")) is None


def _legacy_proposal(world, *, confirmed=False, kind="obligation"):
    """Historical pre-fix data; no pretend protocol identity is manufactured."""
    lifecycle = ManagementLifecycle()
    scope = lifecycle.scope(world, project_id=4, actor_user_id=2)
    if kind == "decision":
        row = lifecycle.create_decision(world, scope=scope, question="Synthetic legacy decision?",
                                        owner_user_id=2, evidence_pins=[evidence_pin()])
    else:
        row = lifecycle.create_obligation(world, scope=scope, title="Synthetic legacy obligation",
                                          owner_user_id=2, evidence_pins=[evidence_pin()])
    if confirmed:
        if kind == "decision":
            row = lifecycle.transition_governance(world, scope=scope, entity_type=kind,
                entity_id=row.id, expected_version=1, status="confirmed")
        else:
            row = lifecycle.transition_obligation(world, scope=scope, obligation_id=row.id,
                expected_version=1, status="confirmed")
    world.add(ManagementProposalOrigin(project_id=4, origin_type="meeting", origin_id=1,
        entity_type=kind, entity_id=row.id, proposal_kind=kind,
        evidence_pins=[evidence_pin()], created_by_user_id=2))
    world.commit()
    return row


@pytest.mark.parametrize("kind", ["obligation", "decision"])
def test_overwritten_protocol_cannot_authorize_existing_proposal_confirmation(world, kind):
    row = _legacy_proposal(world, kind=kind)
    meeting = world.get(Meeting, 1)
    meeting.minutes = "Entirely replaced synthetic protocol"
    world.commit()
    before = row.record_version
    with pytest.raises(ManagementDenied, match="invalid_meeting_source"):
        MeetingProposalService().confirm(world, project_id=4, actor_user_id=2,
            entity_type=kind, entity_id=row.id, expected_version=before,
            create_internal_task=(kind == "obligation"))
    assert row.status == "needs_confirmation" and row.record_version == before
    assert world.scalars(select(Task)).all() == []


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("meeting_status", ["completed", "cancelled"])
def test_legacy_meeting_history_is_visible_with_explicit_invalid_origin(world, confirmed, meeting_status):
    row = _legacy_proposal(world, confirmed=confirmed)
    world.get(Meeting, 1).status = meeting_status
    world.get(Meeting, 1).minutes = "Replaced protocol; candidate Evidence remains current"
    world.commit()
    before = row.record_version
    listed = MeetingProposalService().list_for_origin(world, project_id=4,
        actor_user_id=2, origin_type="meeting", origin_id=1)
    assert listed[0]["entity_id"] == row.id
    assert listed[0]["origin_status"] == "invalid_source"
    assert listed[0]["origin_reason"] == "meeting_source_binding_required"
    assert listed[0]["confirmation_available"] is False
    assert row.record_version == before
    assert row.status == ("confirmed" if confirmed else "needs_confirmation")


@pytest.mark.parametrize("change", ["source_revoked", "stale", "current_changed"])
def test_meeting_history_rechecks_current_evidence_authority(world, change):
    row = _legacy_proposal(world, confirmed=True)
    row_id, row_version = row.id, row.record_version
    if change == "source_revoked":
        world.get(SourceReference, uid(13)).availability = "revoked"
    elif change == "stale":
        world.get(EvidenceAssessment, uid(16)).freshness = "stale"
    else:
        previous = world.get(SourceVersion, uid(15))
        world.add(SourceVersion(id=uid(901), organization_id=1, source_id=uid(13),
            observation_key="synthetic-replacement", provider_revision="replacement-v2",
            consistency="revision_bound", locator_at_observation=previous.locator_at_observation,
            integrity=[], observed_at=previous.observed_at))
        world.flush()
        world.get(SourceCurrent, uid(13)).version_id = uid(901)
    world.commit()
    with pytest.raises(ManagementDenied, match="resource_unavailable"):
        MeetingProposalService().list_for_origin(world, project_id=4,
            actor_user_id=2, origin_type="meeting", origin_id=1)
    assert world.get(Obligation, row_id).record_version == row_version
    assert world.get(Obligation, row_id).status == "confirmed"
    assert len(world.scalars(select(ManagementProposalOrigin)).all()) == 1


def test_api_denies_unbound_candidates_but_still_saves_minutes(world):
    world.commit()
    user = world.get(User, 2)
    with pytest.raises(HTTPException) as failure:
        management.propose_meeting_actions(1, management.EvidenceProposalCreate(
            project_id=4, candidates=[candidate()]), world, user)
    assert failure.value.status_code == 422 and failure.value.detail == "invalid_meeting_source"

    result = management.finish_meeting(1, management.MeetingUpdate(
        minutes="New synthetic protocol", status="completed"), world, user)
    assert result["proposal_state"] == "invalid_source"
    assert result["origin_reason"] == "meeting_source_binding_required"
    assert world.get(Meeting, 1).minutes == "New synthetic protocol"
    assert result["tasks"] == result["risks"] == result["decisions"] == 0
    assert all("synthetic protocol" not in (row.details or "") for row in world.scalars(select(AuditLog)))


def test_alternate_api_confirmation_and_task_mapping_cannot_bypass_origin(world):
    row = _legacy_proposal(world)
    user = world.get(User, 2)
    with pytest.raises(HTTPException) as failure:
        management.transition_evidence_obligation(row.id,
            management.LifecycleTransition(expected_version=1, status="confirmed"), world, user)
    assert failure.value.detail == "invalid_meeting_source"
    assert row.status == "needs_confirmation"

    # Previously confirmed historical rows remain visible, but do not authorize
    # a new materialization without a protocol version/source binding.
    scope = ManagementLifecycle().scope(world, project_id=4, actor_user_id=2)
    row = ManagementLifecycle().transition_obligation(world, scope=scope,
        obligation_id=row.id, expected_version=1, status="confirmed")
    world.commit()
    with pytest.raises(HTTPException) as failure:
        management.map_obligation_task(row.id, row.record_version, world, user)
    assert failure.value.detail == "invalid_meeting_source"
    assert world.scalars(select(Task)).all() == []


@pytest.mark.parametrize("status", ["confirmed", "in_progress", "fulfilled", "breached"])
def test_legacy_obligation_api_cannot_promote_unbound_meeting_proposal(world, status):
    row = _legacy_proposal(world)
    with pytest.raises(HTTPException) as failure:
        management.update_obligation(row.id, management.ObligationUpdate(
            status=status, result_note="Synthetic result"), world, world.get(User, 2))
    assert failure.value.status_code == 422 and failure.value.detail == "invalid_meeting_source"
    assert row.status == "needs_confirmation"


@pytest.mark.parametrize("status", ["confirmed", "decided", "executed"])
def test_legacy_decision_api_cannot_promote_unbound_meeting_proposal(world, status):
    row = _legacy_proposal(world, kind="decision")
    with pytest.raises(HTTPException) as failure:
        governance.update_decision(row.id, governance.DecisionUpdate(
            status=status, decision_text="Synthetic decision"), world, world.get(User, 2))
    assert failure.value.status_code == 422 and failure.value.detail == "invalid_meeting_source"
    assert row.status == "needs_confirmation"


def test_v2_decision_confirmation_cannot_bypass_meeting_origin(world):
    row = _legacy_proposal(world, kind="decision")
    with pytest.raises(HTTPException) as failure:
        management.transition_governance("decisions", row.id, management.GovernanceTransition(
            project_id=4, expected_version=1, status="confirmed"), world, world.get(User, 2))
    assert failure.value.detail == "invalid_meeting_source"
    assert row.status == "needs_confirmation"


def test_manual_nonmeeting_obligation_and_decision_still_confirm_and_materialize(world):
    lifecycle = ManagementLifecycle()
    scope = lifecycle.scope(world, project_id=4, actor_user_id=2)
    obligation = lifecycle.create_obligation(world, scope=scope, title="Manual evidence obligation",
        owner_user_id=2, evidence_pins=[evidence_pin()])
    decision = lifecycle.create_decision(world, scope=scope, question="Manual evidence decision?",
        owner_user_id=2, evidence_pins=[evidence_pin()])
    world.commit()
    actor = world.get(User, 2)
    result = management.transition_evidence_obligation(obligation.id,
        management.LifecycleTransition(expected_version=1, status="confirmed"), world, actor)
    assert result["status"] == "confirmed"
    mapped = management.map_obligation_task(obligation.id, result["record_version"], world, actor)
    assert world.get(Task, mapped["id"]) is not None
    result = governance.update_decision(decision.id, governance.DecisionUpdate(status="confirmed"), world, actor)
    assert result["status"] == "confirmed"


def test_confirmed_legacy_meeting_business_history_remains_editable_without_new_confirmation(world):
    row = _legacy_proposal(world, confirmed=True)
    result = management.update_obligation(row.id, management.ObligationUpdate(status="in_progress"),
        world, world.get(User, 2))
    assert result["status"] == "in_progress"
    listing = management.list_meeting_actions(1, 4, world, world.get(User, 2))
    assert listing["origin_status"] == "invalid_source"
    assert listing["proposals"][0]["status"] == "in_progress"
