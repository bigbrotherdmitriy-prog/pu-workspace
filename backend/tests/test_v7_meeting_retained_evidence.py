"""Real synthetic XLSX durable ingestion -> meeting retained proof, no byte reads."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.models.management import Meeting
from app.models.materialization import Materialization
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceVersion
from app.models.v54_authority import AuthorityState
from app.models.task import Task
from app.mvp3.lifecycle import ManagementDenied
from app.mvp3.meeting_source_binding import MeetingSourceBindingService
from test_mvp1_xlsx_durable_evidence import xlsx_world, local_source_world, wired, stage_xlsx, run_xlsx, cells
from test_mvp3_meeting_source_binding import _bind, _propose


@pytest.fixture
def retained_meeting(xlsx_world, monkeypatch):
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    sessions, runtime, _, _ = xlsx_world
    with sessions.begin() as db:
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        assert original.state == "PURGED"
        proof = cells(db)[0]
        meeting = Meeting(project_id=4, created_by_user_id=2, title="Synthetic retained protocol",
                          minutes="Recorded human protocol", status="completed")
        db.add(meeting); db.flush()
        ids = dict(meeting_id=meeting.id, source_id=original.source_id,
                   source_version_id=original.source_version_id, evidence_id=proof.id,
                   original_evidence_id=original.evidence_id, child_id=proof.representation_ref["representation_id"])
    monkeypatch.setattr(runtime.storage, "read_chunks", lambda *a, **k: pytest.fail("metadata opened bytes"))
    return sessions, ids


def test_retained_child_catalog_binding_and_proposal_after_original_purge(retained_meeting):
    sessions, ids = retained_meeting
    with sessions.begin() as db:
        service = MeetingSourceBindingService()
        catalog = service.eligible(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2)
        assert len(catalog["sources"]) == 1
        assert len(catalog["sources"][0]["evidence_pins"]) == 3
        binding = _bind(db, ids)
        proposal = _propose(db, ids, binding)
        assert proposal["entity_type"] == "obligation"
        assert service.origin(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2)["confirmation_available"]


@pytest.mark.parametrize("change", ["child_purged", "assessment_expired", "original_pin", "manifest_pin",
                                    "descriptor_handle", "source_current", "authority", "assessment_future"])
def test_exact_child_rechecked_not_replaced_by_other_available_child(retained_meeting, change):
    sessions, ids = retained_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
    with sessions.begin() as db:
        if change == "child_purged":
            db.execute(update(Materialization).where(Materialization.id == ids["child_id"]).values(state="PURGED",
                       wrapped_dek=None, format_version=None, chunk_size=None,
                       expired_at=datetime.now(timezone.utc), purged_at=datetime.now(timezone.utc),
                       manifest={"schema_version": "v54.materialization.tombstone.1"}))
        elif change == "assessment_expired":
            db.execute(update(EvidenceAssessment).where(EvidenceAssessment.evidence_id == ids["evidence_id"])
                       .values(valid_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        elif change == "original_pin":
            ids = {**ids, "evidence_id": ids["original_evidence_id"]}
        elif change == "manifest_pin":
            row = db.get(Materialization, ids["child_id"])
            row.manifest = {**row.manifest, "evidence_pin": {**row.manifest["evidence_pin"],
                "ref": {**row.manifest["evidence_pin"]["ref"], "id": {"kind": "uuid", "value": ids["original_evidence_id"]}}}}
        elif change == "descriptor_handle":
            row = db.get(Evidence, ids["evidence_id"])
            db.execute(update(Evidence).where(Evidence.id == row.id).values(
                representation_ref={**row.representation_ref, "handle": "0" * 32}))
        elif change == "source_current":
            old = db.get(SourceVersion, ids["source_version_id"])
            newer = SourceVersion(id=str(uuid4()), organization_id=1, source_id=ids["source_id"],
                revision=1, observation_key="synthetic-newer", consistency=old.consistency,
                locator_at_observation=old.locator_at_observation, integrity=old.integrity,
                observed_at=old.observed_at)
            db.add(newer); db.flush()
            db.execute(update(SourceCurrent).where(SourceCurrent.source_id == ids["source_id"]).values(version_id=newer.id))
        elif change == "authority":
            db.execute(update(AuthorityState).values(state="revoked"))
        else:
            db.execute(update(EvidenceAssessment).where(EvidenceAssessment.evidence_id == ids["evidence_id"])
                       .values(checked_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    with sessions.begin() as db:
        with pytest.raises(ManagementDenied):
            _propose(db, ids, binding)


def test_confirmation_rechecks_exact_child_after_proposal(retained_meeting):
    from app.mvp3.meeting_digest import MeetingProposalService
    sessions, ids = retained_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
        proposal = _propose(db, ids, binding)
    with sessions.begin() as db:
        db.execute(update(EvidenceAssessment).where(EvidenceAssessment.evidence_id == ids["evidence_id"])
                   .values(availability="unavailable"))
    with sessions.begin() as db:
        with pytest.raises(ManagementDenied):
            MeetingProposalService().confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
                entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
        assert list(db.scalars(select(Task))) == []


def test_retained_unverified_child_requires_explicit_confirmation_for_task(retained_meeting):
    from app.mvp3.meeting_digest import MeetingProposalService
    sessions, ids = retained_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
        proposal = _propose(db, ids, binding)
        assert list(db.scalars(select(Task))) == []
    with sessions.begin() as db:
        result = MeetingProposalService().confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
    with sessions.begin() as db:
        assert MeetingProposalService().confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
            entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True) == result
        assert len(list(db.scalars(select(Task)))) == 1


def test_mixed_original_and_child_pins_are_denied(retained_meeting):
    from test_mvp3_meeting_source_binding import _candidate
    sessions, ids = retained_meeting
    with sessions.begin() as db:
        binding_dict = _bind(db, ids)
        service = MeetingSourceBindingService()
        binding, scope = service.require(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2,
                                        binding_id=binding_dict["binding_id"])
        pins = _candidate(ids).evidence_pins + _candidate({**ids, "evidence_id": ids["original_evidence_id"]}).evidence_pins
        with pytest.raises(ManagementDenied):
            service.evidence(db, scope, binding, pins)


@pytest.mark.parametrize("mutation", ["version", "kind", "namespace", "tenant", "locator"])
def test_retained_descriptor_strict_exact_pins(retained_meeting, mutation):
    sessions, ids = retained_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
    with sessions.begin() as db:
        row = db.get(Evidence, ids["evidence_id"])
        value = {**row.representation_ref}
        if mutation == "version":
            value["source_version_pin"] = {**value["source_version_pin"], "value": 2}
        elif mutation == "kind":
            value["source_ref"] = {**value["source_ref"], "type": "evidence"}
        elif mutation == "namespace":
            value["source_ref"] = {**value["source_ref"], "namespace": "external"}
        elif mutation == "tenant":
            value["source_ref"] = {**value["source_ref"], "tenant_id": {"kind": "int", "value": "2"}}
        else:
            # Descriptor has no locator field: reject rather than accept a
            # second, conflicting location alongside the immutable evidence.
            value["locator"] = {"kind": "sheet_cell", "range_a1": "Z999"}
        db.execute(update(Evidence).where(Evidence.id == row.id).values(representation_ref=value))
    with sessions.begin() as db:
        with pytest.raises(ManagementDenied):
            _propose(db, ids, binding)


def test_pending_child_change_is_not_flushed_or_refreshed_by_metadata_fallback(retained_meeting, monkeypatch):
    from app.mvp3.lifecycle import ManagementScope
    sessions, ids = retained_meeting
    with sessions() as db:
        row = db.get(Materialization, ids["child_id"])
        row.retention_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        monkeypatch.setattr(db, "flush", lambda *a, **k: pytest.fail("metadata flushed pending state"))
        with pytest.raises(ManagementDenied):
            MeetingSourceBindingService()._source(db, ManagementScope(1, 4, 2, "owner"),
                                                 ids["source_id"], ids["source_version_id"])
        assert row in db.dirty
        assert row.retention_until < datetime.now(timezone.utc)
