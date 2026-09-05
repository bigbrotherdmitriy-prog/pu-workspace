"""Exact meeting protocol binding contracts; no provider calls or content reads."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.models.management import Meeting
from app.models.management import Obligation, ObligationHistory
from app.models.management_digest import ManagementProposalOrigin
from app.models.materialization import Materialization
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskHistory
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import EvidenceAssessment, SourceReference
from app.models.meeting_source_binding import MeetingSourceBinding
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied
from app.mvp3.meeting_source_binding import MeetingSourceBindingService
from app.mvp3.meeting_digest import MeetingActionCandidate, MeetingProposalService
from test_mvp1_local_source_authority import local_source_world, wired, _stage  # noqa: F401
from test_mvp3_meeting_digest import world  # noqa: F401
from v54_pilot_fixture import uid


def test_unknown_source_authority_cannot_bind_or_list(world):
    service = MeetingSourceBindingService()
    assert service.eligible(world, project_id=4, meeting_id=1, actor_user_id=2)["sources"] == []
    with pytest.raises(ManagementDenied):
        service.bind(world, project_id=4, meeting_id=1, actor_user_id=2,
            expected_version=1, command_id=str(uuid4()), source_id=uid(13), source_version_id=uid(15))
    assert world.scalars(select(MeetingSourceBinding)).all() == []
    assert world.get(Meeting, 1).record_version == 1


def test_minutes_edit_is_cas_and_stale_write_does_not_overwrite(world):
    service = MeetingSourceBindingService()
    service.edit(world, project_id=4, meeting_id=1, actor_user_id=2,
        expected_version=1, minutes="Reviewed protocol", status="completed")
    world.commit()
    with pytest.raises(ManagementConflict):
        service.edit(world, project_id=4, meeting_id=1, actor_user_id=2,
            expected_version=1, minutes="Stale protocol", status="completed")
    world.rollback()
    assert world.get(Meeting, 1).record_version == 2
    assert world.get(Meeting, 1).minutes == "Reviewed protocol"


@pytest.fixture
def local_meeting(local_source_world):
    _, sessions, runtime, _, _, _ = local_source_world
    queued = _stage(sessions)
    with sessions.begin() as db:
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        meeting = Meeting(project_id=4, created_by_user_id=2, title="Local protocol",
            minutes="Human recorded protocol", status="completed")
        db.add(meeting)
        db.add(EvidenceAssessment(organization_id=1, evidence_id=original.evidence_id,
            freshness="fresh", availability="available", verification="unverified",
            checked_at=datetime.now(timezone.utc), valid_until=datetime.now(timezone.utc) + timedelta(minutes=10)))
        db.flush()
        ids = dict(meeting_id=meeting.id, source_id=original.source_id,
            source_version_id=original.source_version_id, evidence_id=original.evidence_id)
    return sessions, runtime, ids


def _bind(db, ids, *, version=1, command=None):
    return MeetingSourceBindingService().bind(db, project_id=4, actor_user_id=2,
        meeting_id=ids["meeting_id"], source_id=ids["source_id"], source_version_id=ids["source_version_id"],
        expected_version=version, command_id=command or str(uuid4()))


def _candidate(ids, kind="task"):
    return MeetingActionCandidate(kind=kind, title="Action from exact local protocol", owner_user_id=2,
        evidence_pins=[{"ref": {"namespace": "pu", "type": "evidence", "tenant_id": {"kind": "int", "value": "1"},
            "id": {"kind": "uuid", "value": ids["evidence_id"]}}, "version_kind": "revision", "value": 1}])


def _propose(db, ids, binding, kind="task"):
    return MeetingProposalService().propose(db, project_id=4, actor_user_id=2,
        meeting_id=ids["meeting_id"], meeting_source_binding_id=binding["binding_id"], candidates=[_candidate(ids, kind)])[0]


def test_actual_local_binding_to_confirmation_creates_one_internal_task(local_meeting, monkeypatch):
    sessions, runtime, ids = local_meeting
    monkeypatch.setattr(runtime.storage, "read_chunks", lambda *_a, **_k: pytest.fail("binding opened source bytes"))
    command = str(uuid4())
    with sessions.begin() as db:
        catalog = MeetingSourceBindingService().eligible(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2)
        assert catalog["sources"] == [{"source_id": ids["source_id"], "source_version_id": ids["source_version_id"],
            "evidence_pins": _candidate(ids).evidence_pins}]
        binding = _bind(db, ids, command=command)
        assert _bind(db, ids, command=command) == binding
        proposal = _propose(db, ids, binding)
        assert _propose(db, ids, binding) == proposal
        assert db.scalars(select(Task)).all() == []
    with sessions.begin() as db:
        service = MeetingProposalService()
        first = service.confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
    with sessions.begin() as db:
        replay = service.confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
        assert replay == first
        assert len(db.scalars(select(Task)).all()) == 1
        assert len(db.scalars(select(TaskHistory)).all()) == 1
        assert len(db.scalars(select(ObligationHistory)).all()) == 2
        assert len(db.scalars(select(MeetingSourceBinding)).all()) == 1
        assert len(db.scalars(select(ManagementProposalOrigin)).all()) == 1


def test_edit_invalidates_old_binding_and_rebind_does_not_relabel_history(local_meeting):
    sessions, _, ids = local_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
        proposal = _propose(db, ids, binding)
    with sessions.begin() as db:
        MeetingSourceBindingService().edit(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2,
            expected_version=2, minutes="A new human protocol", status="completed")
    with sessions.begin() as db:
        with pytest.raises(ManagementDenied):
            MeetingProposalService().confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
                entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
        history = MeetingProposalService().list_for_origin(db, project_id=4, actor_user_id=2,
            origin_type="meeting", origin_id=ids["meeting_id"])
        assert history[0]["origin_status"] == "invalid_source"
        newer = _bind(db, ids, version=3)
        assert newer["binding_id"] != binding["binding_id"]
        with pytest.raises(ManagementDenied):
            _propose(db, ids, newer)
        assert db.scalar(select(ManagementProposalOrigin)).meeting_source_binding_id == binding["binding_id"]
        assert db.scalars(select(Task)).all() == []


@pytest.mark.parametrize("change", ["source_revoked", "role_revoked", "mandate_revoked", "assessment_expired"])
def test_bound_effect_and_read_recheck_live_authority(local_meeting, change):
    sessions, _, ids = local_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
        proposal = _propose(db, ids, binding)
    with sessions.begin() as db:
        if change == "source_revoked":
            db.execute(update(SourceReference).where(SourceReference.id == ids["source_id"]).values(availability="revoked"))
        elif change == "role_revoked":
            db.execute(update(ProjectMember).where(ProjectMember.project_id == 4).values(role="viewer"))
        elif change == "mandate_revoked":
            db.execute(update(AuthorityState).values(state="revoked"))
        else:
            db.execute(update(EvidenceAssessment).where(EvidenceAssessment.evidence_id == ids["evidence_id"])
                .values(valid_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    with sessions.begin() as db:
        with pytest.raises(ManagementDenied):
            MeetingProposalService().confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
                entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
        with pytest.raises(ManagementDenied):
            MeetingProposalService().list_for_origin(db, project_id=4, actor_user_id=2,
                origin_type="meeting", origin_id=ids["meeting_id"])
        assert db.get(Obligation, proposal["entity_id"]).status == "needs_confirmation"
        assert db.scalars(select(Task)).all() == []


def test_binding_command_and_meeting_version_are_exact(local_meeting):
    sessions, _, ids = local_meeting
    command = str(uuid4())
    with sessions.begin() as db:
        _bind(db, ids, command=command)
    with sessions.begin() as db:
        with pytest.raises(ManagementConflict):
            _bind(db, ids, version=2, command=command)
        with pytest.raises(ManagementConflict):
            _bind(db, ids, version=1)
        assert db.get(Meeting, ids["meeting_id"]).record_version == 2
        assert len(db.scalars(select(MeetingSourceBinding)).all()) == 1


@pytest.mark.parametrize("actor,project", [(3, 4), (2, 9)])
def test_binding_requires_exact_human_owner_project_and_manager(local_meeting, actor, project):
    sessions, _, ids = local_meeting
    with sessions.begin() as db:
        with pytest.raises(ManagementDenied):
            MeetingSourceBindingService().bind(db, project_id=project, actor_user_id=actor,
                meeting_id=ids["meeting_id"], source_id=ids["source_id"], source_version_id=ids["source_version_id"],
                expected_version=1, command_id=str(uuid4()))
        assert db.scalars(select(MeetingSourceBinding)).all() == []


@pytest.mark.parametrize("kind", ["task", "decision"])
def test_candidate_must_match_bound_source_and_generic_paths_recheck(local_meeting, kind):
    from app.api import governance, management
    from app.models.user import User
    from fastapi import HTTPException
    from test_mvp3_meeting_digest import candidate
    sessions, _, ids = local_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
        with pytest.raises(ManagementDenied):
            MeetingProposalService().propose(db, project_id=4, actor_user_id=2,
                meeting_id=ids["meeting_id"], meeting_source_binding_id=binding["binding_id"], candidates=[candidate(kind)])
        proposal = _propose(db, ids, binding, kind)
    with sessions() as db:
        user = db.get(User, 2)
        with pytest.raises(HTTPException) as failure:
            if kind == "task":
                management.update_obligation(proposal["entity_id"], management.ObligationUpdate(status="confirmed"), db, user)
            else:
                governance.update_decision(proposal["entity_id"], governance.DecisionUpdate(status="confirmed"), db, user)
        assert failure.value.status_code == 409
        db.rollback()
    with sessions.begin() as db:
        MeetingSourceBindingService().edit(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2,
            expected_version=2, minutes="Protocol changed", status="completed")
    with sessions() as db:
        user = db.get(User, 2)
        with pytest.raises(HTTPException) as failure:
            if kind == "task":
                management.transition_evidence_obligation(proposal["entity_id"],
                    management.LifecycleTransition(status="confirmed", expected_version=1), db, user)
            else:
                management.transition_governance("decisions", proposal["entity_id"],
                    management.GovernanceTransition(project_id=4, status="confirmed", expected_version=1), db, user)
        assert failure.value.status_code == 422
        assert db.scalars(select(Task)).all() == []


def test_api_contract_returns_exact_binding_pins_and_required_meeting_cas(local_meeting):
    from app.api import management
    from app.models.user import User
    from pydantic import ValidationError
    sessions, _, ids = local_meeting
    with pytest.raises(ValidationError):
        management.MeetingUpdate(minutes="Old flagless client")
    with sessions() as db:
        user = db.get(User, 2)
        bound = management.bind_meeting_source(ids["meeting_id"], management.MeetingSourceBind(
            project_id=4, expected_version=1, command_id=uuid4(), source_id=ids["source_id"],
            source_version_id=ids["source_version_id"]), db, user)
        result = management.propose_meeting_actions(ids["meeting_id"], management.MeetingProposalCreate(
            project_id=4, meeting_source_binding_id=bound["binding_id"], candidates=[_candidate(ids)]), db, user)
        for key in ("binding_id", "meeting_id", "meeting_record_version", "source_id", "source_version_id"):
            assert result[key] == result["proposals"][0][key] == bound[key]
        confirmed = management.confirm_evidence_proposal("obligation", result["proposals"][0]["entity_id"],
            management.EvidenceProposalConfirm(project_id=4, expected_version=1, create_internal_task=True), db, user)
        assert confirmed["proposal"]["binding_id"] == bound["binding_id"]
        assert confirmed["proposal"]["origin_status"] == "bound"
        assert confirmed["proposal"]["status"] == "confirmed"
        assert confirmed["external_actions_created"] is False
        edited = management.finish_meeting(ids["meeting_id"], management.MeetingUpdate(
            expected_version=2, minutes="Edited human protocol"), db, user)
        assert edited["record_version"] == 3 and edited["confirmation_available"] is False


def test_binding_and_origin_remain_append_only(local_meeting):
    sessions, _, ids = local_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
        _propose(db, ids, binding)
    with sessions() as db:
        row = db.get(MeetingSourceBinding, binding["binding_id"])
        row.source_version_id = str(uuid4())
        with pytest.raises(ValueError, match="append_only"):
            db.flush()
        db.rollback()
        link = db.scalar(select(ManagementProposalOrigin))
        link.meeting_source_binding_id = None
        with pytest.raises(ValueError, match="append_only"):
            db.flush()
        db.rollback()


def test_stale_confirmation_cannot_add_a_task_to_already_confirmed_meeting_proposal(local_meeting):
    sessions, _, ids = local_meeting
    service = MeetingProposalService()
    with sessions.begin() as db:
        proposal = _propose(db, ids, _bind(db, ids))
    with sessions.begin() as db:
        confirmed = service.confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=proposal["entity_id"], expected_version=1, create_internal_task=False)
    with sessions.begin() as db:
        with pytest.raises(ManagementConflict):
            service.confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
                entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
        assert db.scalars(select(Task)).all() == []
        result = service.confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=proposal["entity_id"], expected_version=confirmed["record_version"], create_internal_task=True)
        assert result["task_id"] is not None
