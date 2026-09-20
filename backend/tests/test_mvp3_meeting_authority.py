from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.management import (
    MeetingUpdate, finish_meeting, meeting_proposals, meeting_source_candidates, meetings,
)
from app.document_extraction import (
    CombinedExtraction, ObligationCandidate, RawDecisionCandidate, RawRiskCandidate,
)
from app.models.governance import Decision, Risk
from app.models.management import ManagementHistory, Meeting, MeetingProposal, MeetingSourceBinding
from app.models.materialization import Materialization
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion
from app.mvp3 import meeting_proposals as service
from app.mvp3.meeting_proposals import MeetingProposalConflict, MeetingProposalDenied


def _uuid() -> str:
    return str(uuid4())


def _world(db, user_factory):
    organization = Organization(name="Meeting authority tenant")
    db.add(organization); db.flush()
    manager = user_factory(name="Manager")
    viewer = user_factory(name="Viewer")
    project = Project(name="Authority project", organization_id=organization.id)
    db.add(project); db.flush()
    db.add_all([
        ProjectMember(project_id=project.id, user_id=manager.id, role="manager"),
        ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"),
    ])
    meeting = Meeting(
        project_id=project.id, created_by_user_id=manager.id, title="Weekly",
        minutes="old", status="planned",
    )
    db.add(meeting); db.commit()
    return organization, project, manager, viewer, meeting


def _source(db, organization, project, owner):
    now = datetime.now(timezone.utc)
    identity_id, source_id, version_id, evidence_id, materialization_id = (
        _uuid(), _uuid(), _uuid(), _uuid(), _uuid(),
    )
    staging_id = UUID(materialization_id).hex
    identity = ConnectionIdentity(
        id=identity_id, organization_id=organization.id, provider="local_upload",
        account_key="a" * 64, state="verified", binding_epoch=1,
        credential_generation=1, record_version=1, verified_at=now,
    )
    source = SourceReference(
        id=source_id, organization_id=organization.id, origin_project_id=project.id,
        identity_id=identity_id, namespace="local-upload", external_id="b" * 64,
        external_id_kind="stable_id", incarnation=1, object_kind="file",
        canonical_locator={"kind": "opaque_id", "value": staging_id, "normalization_version": "1"},
        record_version=1, freshness="fresh", sync_state="current", availability="available",
        last_seen_at=now, last_checked_at=now, next_check_at=now + timedelta(hours=2),
        policy_pins={"access": {}, "retention": {}, "residency": {}},
        residency={"source_location": "test"},
    )
    version = SourceVersion(
        id=version_id, organization_id=organization.id, source_id=source_id, revision=1,
        observation_key="b" * 64, provider_revision="c" * 64,
        consistency="digest_observed",
        locator_at_observation={"kind": "local_upload", "staging_id": staging_id,
                                "display_name": "minutes.txt", "media_type": "text/plain",
                                "size": 64, "fence": "d" * 32},
        integrity=[{"algorithm": "sha256", "value": "e" * 64}], observed_at=now,
    )
    evidence = Evidence(
        id=evidence_id, organization_id=organization.id, source_id=source_id,
        source_version_id=version_id, revision=1,
        locator={"kind": "whole_object", "reason_code": "local_upload"},
        extractor={"name": "local_upload", "version": "1"}, extracted_at=now,
        confidence_kind="unknown", policy_pins=source.policy_pins,
    )
    materialization = Materialization(
        id=materialization_id, organization_id=organization.id, project_id=project.id,
        owner_id=owner.id, source_id=source_id, source_version_id=version_id,
        evidence_id=evidence_id, parent_id=None, object_id="f" * 32, state="DERIVED",
        record_version=4, active_fence=None, kek_reference="test", kek_version="v1",
        format_version=1, chunk_size=1024, wrapped_dek="wrapped",
        manifest={"storage": {"object_id": "f" * 32}}, residency="test",
        retention_until=now + timedelta(hours=2), copy_allowed=False, derive_allowed=True,
        admitted_at=now, writing_at=now, sealed_at=now, derived_at=now,
    )
    db.add_all([identity, source]); db.flush()
    db.add(version); db.flush()
    db.add_all([SourceCurrent(
        source_id=source_id, organization_id=organization.id, version_id=version_id,
    ), evidence]); db.flush()
    db.add(materialization)
    db.commit()
    return {"source_id": source_id, "source_version_id": version_id,
            "evidence_id": evidence_id, "materialization_id": materialization_id}


@pytest.fixture
def action_extraction(monkeypatch):
    extraction = CombinedExtraction(obligations=[ObligationCandidate(
        title="Подготовить протокол до 25.09.2026", excerpt="Подготовить протокол до 25.09.2026",
        due_date=datetime(2026, 9, 25).date(), due_date_evidence_quote="до 25.09.2026",
        assignee_hint=None, assignee_evidence_quote=None, amount=None,
        amount_currency=None, amount_evidence_quote=None, confidence=0.9,
        extraction_method="regex",
    )], extraction_method="regex")
    monkeypatch.setattr(service, "extract_for_text", lambda *_args, **_kwargs: extraction)
    return extraction


def test_minutes_create_no_actions_before_exact_binding(db_session, user_factory):
    _, _, manager, _, meeting = _world(db_session, user_factory)
    result = finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить протокол до 25.09.2026", status="completed",
    ), db_session, manager)
    assert result["proposal_state"] == "source_binding_required"
    assert result["tasks"] == result["risks"] == result["decisions"] == 0
    assert db_session.query(Task).count() == 0
    assert db_session.query(MeetingProposal).count() == 0


def test_exact_current_source_creates_proposal_and_confirmation_is_idempotent(
    db_session, user_factory, action_extraction,
):
    organization, project, manager, _, meeting = _world(db_session, user_factory)
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить протокол до 25.09.2026", status="completed",
    ), db_session, manager)
    source = _source(db_session, organization, project, manager)
    command_id = _uuid()
    result = service.bind_current_source(
        db_session, meeting_id=meeting.id, actor_user_id=manager.id,
        expected_record_version=2, command_id=command_id, **source,
    )
    db_session.commit()
    assert result["proposal_count"] == 1
    assert result["external_actions_created"] is False
    assert db_session.query(Task).count() == 0
    proposal_id = result["proposals"][0]["id"]
    replay_binding = service.bind_current_source(
        db_session, meeting_id=meeting.id, actor_user_id=manager.id,
        expected_record_version=2, command_id=command_id, **source,
    )
    assert replay_binding["binding_id"] == result["binding_id"]
    assert db_session.query(MeetingSourceBinding).count() == 1
    confirmation_id = _uuid()
    confirmed = service.confirm_proposal(
        db_session, proposal_id=proposal_id, actor_user_id=manager.id,
        expected_record_version=1, command_id=confirmation_id,
    )
    db_session.commit()
    replay = service.confirm_proposal(
        db_session, proposal_id=proposal_id, actor_user_id=manager.id,
        expected_record_version=1, command_id=confirmation_id,
    )
    db_session.commit()
    assert confirmed["status"] == replay["status"] == "confirmed"
    assert confirmed["target_entity_id"] == replay["target_entity_id"]
    assert db_session.query(Task).count() == 1
    history = db_session.query(ManagementHistory).filter_by(
        entity_type="meeting_proposal", entity_id=proposal_id, action="confirmed",
    ).one()
    assert history.evidence["source_version_id"] == source["source_version_id"]


def test_manager_can_list_only_current_local_upload_candidates_and_viewer_cannot(
    db_session, user_factory,
):
    organization, project, manager, viewer, meeting = _world(db_session, user_factory)
    source = _source(db_session, organization, project, manager)
    listed = meeting_source_candidates(meeting.id, 100, db_session, manager)
    assert listed["count"] == 1
    assert listed["candidates"] == [{
        "document_id": None,
        "display_name": "minutes.txt",
        "media_type": "text/plain",
        **source,
        "observed_at": db_session.get(SourceVersion, source["source_version_id"]).observed_at,
    }]
    with pytest.raises(HTTPException) as denied:
        meeting_source_candidates(meeting.id, 100, db_session, viewer)
    assert denied.value.status_code == 403
    db_session.get(SourceReference, source["source_id"]).freshness = "stale"
    db_session.commit()
    assert meeting_source_candidates(meeting.id, 100, db_session, manager) == {
        "candidates": [], "count": 0,
    }


def test_meeting_permissions_and_proposal_read_model_are_truthful(
    db_session, user_factory, action_extraction,
):
    organization, project, manager, viewer, meeting = _world(db_session, user_factory)
    editor = user_factory(name="Editor")
    db_session.add(ProjectMember(project_id=project.id, user_id=editor.id, role="editor"))
    db_session.commit()
    manager_row = meetings(project.id, db=db_session, user=manager)["meetings"][0]
    editor_row = meetings(project.id, db=db_session, user=editor)["meetings"][0]
    viewer_row = meetings(project.id, db=db_session, user=viewer)["meetings"][0]
    assert manager_row["can_edit"] is manager_row["can_manage"] is True
    assert editor_row["can_edit"] is True
    assert editor_row["can_manage"] is False
    assert viewer_row["can_edit"] is viewer_row["can_manage"] is False
    with pytest.raises(HTTPException) as editor_denied:
        meeting_source_candidates(meeting.id, 100, db_session, editor)
    assert editor_denied.value.status_code == 403
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить протокол до 25.09.2026", status="completed",
    ), db_session, manager)
    source = _source(db_session, organization, project, manager)
    service.bind_current_source(
        db_session, meeting_id=meeting.id, actor_user_id=manager.id,
        expected_record_version=2, command_id=_uuid(), **source,
    )
    db_session.commit()
    payload = meeting_proposals(meeting.id, db_session, viewer)
    assert payload["count"] == 1
    assert payload["source_binding"] == {
        **source,
        "display_name": "minutes.txt",
        "media_type": "text/plain",
        "observed_at": db_session.get(SourceVersion, source["source_version_id"]).observed_at,
    }


def test_binding_materializes_all_candidate_kinds_as_proposals_only(
    db_session, user_factory, monkeypatch,
):
    organization, project, manager, _, meeting = _world(db_session, user_factory)
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить акт. Риск задержки. Требуется решение.",
        status="completed",
    ), db_session, manager)
    source = _source(db_session, organization, project, manager)
    monkeypatch.setattr(service, "extract_for_text", lambda *_args, **_kwargs: CombinedExtraction(
        obligations=[ObligationCandidate(
            title="Подготовить акт", excerpt="Подготовить акт", due_date=None,
            due_date_evidence_quote=None, assignee_hint=None, assignee_evidence_quote=None,
            amount=None, amount_currency=None, amount_evidence_quote=None,
            confidence=0.9, extraction_method="regex",
        )],
        risks=[RawRiskCandidate(
            title="Риск задержки", evidence_quote="Риск задержки",
            kind="risk", criticality="medium", confidence=0.8,
        )],
        decisions=[RawDecisionCandidate(
            question="Требуется решение", evidence_quote="Требуется решение", confidence=0.8,
        )],
        extraction_method="regex",
    ))
    result = service.bind_current_source(
        db_session, meeting_id=meeting.id, actor_user_id=manager.id,
        expected_record_version=2, command_id=_uuid(), **source,
    )
    db_session.commit()
    assert result["proposal_count"] == 3
    assert {row["proposal_type"] for row in result["proposals"]} == {"task", "risk", "decision"}
    assert db_session.query(Task).count() == 0
    assert db_session.query(Risk).count() == 0
    assert db_session.query(Decision).count() == 0


def test_binding_fails_closed_for_stale_source_and_viewer(
    db_session, user_factory, action_extraction,
):
    organization, project, manager, viewer, meeting = _world(db_session, user_factory)
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить протокол до 25.09.2026", status="completed",
    ), db_session, manager)
    source = _source(db_session, organization, project, manager)
    db_session.get(SourceReference, source["source_id"]).freshness = "stale"
    db_session.commit()
    with pytest.raises(MeetingProposalDenied, match="resource_unavailable"):
        service.bind_current_source(
            db_session, meeting_id=meeting.id, actor_user_id=manager.id,
            expected_record_version=2, command_id=_uuid(), **source,
        )
    db_session.rollback()
    db_session.get(SourceReference, source["source_id"]).freshness = "fresh"
    db_session.commit()
    with pytest.raises(MeetingProposalDenied, match="resource_unavailable"):
        service.bind_current_source(
            db_session, meeting_id=meeting.id, actor_user_id=viewer.id,
            expected_record_version=2, command_id=_uuid(), **source,
        )


def test_edit_after_binding_invalidates_confirmation(db_session, user_factory, action_extraction):
    organization, project, manager, _, meeting = _world(db_session, user_factory)
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить протокол до 25.09.2026", status="completed",
    ), db_session, manager)
    source = _source(db_session, organization, project, manager)
    bound = service.bind_current_source(
        db_session, meeting_id=meeting.id, actor_user_id=manager.id,
        expected_record_version=2, command_id=_uuid(), **source,
    )
    db_session.commit()
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=3, minutes="Изменённый протокол", status="completed",
    ), db_session, manager)
    with pytest.raises(MeetingProposalDenied, match="stale_meeting_source"):
        service.confirm_proposal(
            db_session, proposal_id=bound["proposals"][0]["id"], actor_user_id=manager.id,
            expected_record_version=1, command_id=_uuid(),
        )


def test_second_confirmation_command_cannot_create_duplicate(
    db_session, user_factory, action_extraction,
):
    organization, project, manager, _, meeting = _world(db_session, user_factory)
    finish_meeting(meeting.id, MeetingUpdate(
        expected_record_version=1, minutes="Подготовить протокол до 25.09.2026", status="completed",
    ), db_session, manager)
    source = _source(db_session, organization, project, manager)
    bound = service.bind_current_source(
        db_session, meeting_id=meeting.id, actor_user_id=manager.id,
        expected_record_version=2, command_id=_uuid(), **source,
    )
    db_session.commit()
    proposal_id = bound["proposals"][0]["id"]
    service.confirm_proposal(db_session, proposal_id=proposal_id, actor_user_id=manager.id,
                             expected_record_version=1, command_id=_uuid())
    db_session.commit()
    with pytest.raises(MeetingProposalConflict, match="already_confirmed"):
        service.confirm_proposal(db_session, proposal_id=proposal_id, actor_user_id=manager.id,
                                 expected_record_version=1, command_id=_uuid())
    assert db_session.query(Task).count() == 1
