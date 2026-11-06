"""Metadata-only synthetic service/DTO checks; no HTTP, photos or real users."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app import defects
from app.mobile_defect import submit_defect_create
from app.mobile_defect_contract import MAX_SAFE_PROJECT_ID, OfflineDefectCreateRequest
from app.mobile_work_log import submit_work_log_create
from app.mobile_work_log_contract import OfflineWorkLogCreateRequest
from app.models.defects import Defect
from app.models.mobile_sync import MobileSyncCommand
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.work_log import WorkLogEntry
from test_mobile_work_log_service import world, count


def envelope(project_id=1, **data):
    return {"client_mutation_id": "synthetic-defect-command", "device_id": "synthetic-device",
            "project_id": project_id, "operation": "defect.create",
            "client_created_at": "2026-10-30T09:00:00+03:00",
            "data": {"title": "Synthetic offline defect", "location": "Synthetic place", **data}}


def command(project, **data):
    return OfflineDefectCreateRequest.model_validate(envelope(project.id, **data))


def test_lost_response_replay_has_one_fact_and_receipt(world):
    db, actor, project, _ = world
    request = command(project)
    first = submit_defect_create(db, actor, request)
    db.commit()
    assert submit_defect_create(db, actor, request) == first
    assert count(db, Defect) == count(db, MobileSyncCommand) == 1
    stored = db.get(Defect, first["entity_id"])
    assert stored.created_by == actor.id and stored.project_id == project.id
    assert first["entity_type"] == "defect" and first["operation"] == "defect.create"
    assert first["result"]["author_user_id"] == actor.id
    assert first["result"]["project_id"] == project.id


def test_outer_rollback_removes_both_fact_and_receipt(world):
    db, actor, project, _ = world
    submit_defect_create(db, actor, command(project))
    db.rollback()
    assert count(db, Defect) == count(db, MobileSyncCommand) == 0
    submit_defect_create(db, actor, command(project))
    db.commit()
    assert count(db, Defect) == 1


@pytest.mark.parametrize("data", [{"title": " "}, {"priority": "invalid"}, {"due_date": "not-a-date"}])
def test_invalid_business_data_has_no_fact_or_receipt(world, data):
    db, actor, project, _ = world
    with pytest.raises(ValueError):
        submit_defect_create(db, actor, command(project, **data))
    assert count(db, Defect) == count(db, MobileSyncCommand) == 0


def test_changed_payload_same_identity_is_rejected(world):
    db, actor, project, _ = world
    submit_defect_create(db, actor, command(project))
    db.commit()
    with pytest.raises(ValueError, match="client_mutation_id_reused"):
        submit_defect_create(db, actor, command(project, title="Synthetic changed title"))
    assert count(db, Defect) == count(db, MobileSyncCommand) == 1


@pytest.mark.parametrize("role", ["viewer", None])
def test_revoked_editor_cannot_replay_cached_receipt(world, role):
    db, actor, project, _ = world
    request = command(project)
    submit_defect_create(db, actor, request)
    db.commit()
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id))
    if role:
        member.role = role
    else:
        db.delete(member)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        submit_defect_create(db, actor, request)
    assert exc.value.status_code == 403
    assert count(db, Defect) == count(db, MobileSyncCommand) == 1


def test_foreign_project_cannot_create(world):
    db, actor, _, other = world
    with pytest.raises(HTTPException) as exc:
        submit_defect_create(db, actor, command(other))
    assert exc.value.status_code == 403 and count(db, Defect) == 0


def test_same_identity_cannot_be_reused_in_another_allowed_project(world):
    db, actor, project, other = world
    submit_defect_create(db, actor, command(project))
    db.add(ProjectMember(project_id=other.id, user_id=actor.id, role="editor"))
    db.commit()
    with pytest.raises(ValueError, match="client_mutation_id_reused"):
        submit_defect_create(db, actor, command(other))
    assert count(db, Defect) == 1


def test_two_actors_have_separate_receipt_namespaces(world):
    db, actor, project, _ = world
    first = submit_defect_create(db, actor, command(project))
    second_actor = User(name="Synthetic second editor", email="second-defect@example.invalid", is_admin=False)
    db.add(second_actor)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=second_actor.id, role="editor"))
    db.commit()
    second = submit_defect_create(db, second_actor, command(project))
    assert first["entity_id"] != second["entity_id"]
    assert second["result"]["author_user_id"] == second_actor.id
    assert count(db, Defect) == count(db, MobileSyncCommand) == 2


def test_two_editors_same_identity_isolated_replay_and_revocation(world):
    db, actor, project, _ = world
    second_actor = User(name="Synthetic second editor", email="second-defect@example.invalid", is_admin=False)
    db.add(second_actor)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=second_actor.id, role="editor"))
    db.commit()
    request = command(project)
    first = submit_defect_create(db, actor, request)
    second = submit_defect_create(db, second_actor, request)
    db.commit()
    assert first["entity_id"] != second["entity_id"]
    assert first["entity_id"] > 0 and second["entity_id"] > 0
    assert first["receipt_id"] != second["receipt_id"]
    assert first["receipt_id"] > 0 and second["receipt_id"] > 0
    first_stored = db.get(Defect, first["entity_id"])
    second_stored = db.get(Defect, second["entity_id"])
    assert first_stored.created_by == actor.id
    assert second_stored.created_by == second_actor.id
    first_receipt = db.get(MobileSyncCommand, first["receipt_id"])
    second_receipt = db.get(MobileSyncCommand, second["receipt_id"])
    assert first_receipt.user_id == actor.id
    assert second_receipt.user_id == second_actor.id
    assert count(db, Defect) == count(db, MobileSyncCommand) == 2
    assert submit_defect_create(db, actor, request) == first
    assert submit_defect_create(db, second_actor, request) == second
    assert count(db, Defect) == count(db, MobileSyncCommand) == 2
    second_member = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == project.id, ProjectMember.user_id == second_actor.id))
    db.delete(second_member)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        submit_defect_create(db, second_actor, request)
    assert exc.value.status_code == 403
    assert submit_defect_create(db, actor, request) == first
    assert count(db, Defect) == count(db, MobileSyncCommand) == 2


def test_same_body_with_new_mutation_identity_is_a_distinct_defect(world):
    db, actor, project, _ = world
    request = command(project)
    first = submit_defect_create(db, actor, request)
    db.commit()
    payload = request.model_dump(mode="json")
    payload["client_mutation_id"] = "synthetic-defect-another-command"
    second = submit_defect_create(db, actor, OfflineDefectCreateRequest.model_validate(payload))
    assert second["entity_id"] != first["entity_id"] and count(db, Defect) == 2


def test_replay_does_not_resurrect_deleted_defect(world):
    db, actor, project, _ = world
    request = command(project)
    first = submit_defect_create(db, actor, request)
    db.commit()
    defects.delete_defect(db, project.id, first["entity_id"])
    db.commit()
    assert submit_defect_create(db, actor, request) == first
    assert count(db, Defect) == 0 and count(db, MobileSyncCommand) == 1


def test_result_is_not_a_mutable_alias_of_persisted_receipt(world):
    db, actor, project, _ = world
    request = command(project)
    result = submit_defect_create(db, actor, request)
    db.commit()
    result["result"]["title"] = "Synthetic client mutation"
    assert submit_defect_create(db, actor, request)["result"]["title"] == "Synthetic offline defect"


def test_failure_after_business_flush_leaves_no_orphan_fact(world, monkeypatch):
    db, actor, project, _ = world
    original = defects.create_defect
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Synthetic failure before receipt")
    monkeypatch.setattr(defects, "create_defect", fail)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        submit_defect_create(db, actor, command(project))
    assert count(db, Defect) == count(db, MobileSyncCommand) == 0


def test_forced_stale_initial_read_rolls_back_provisional_fact_on_real_unique_conflict(world, monkeypatch):
    db, actor, project, _ = world
    request = command(project)
    first = submit_defect_create(db, actor, request)
    db.commit()
    original = db.scalar
    missed = False
    def stale_read(statement, *args, **kwargs):
        nonlocal missed
        if not missed and any(d.get("entity") is MobileSyncCommand for d in getattr(statement, "column_descriptions", [])):
            missed = True
            return None
        return original(statement, *args, **kwargs)
    monkeypatch.setattr(db, "scalar", stale_read)
    assert submit_defect_create(db, actor, request) == first
    assert missed and count(db, Defect) == count(db, MobileSyncCommand) == 1


@pytest.mark.parametrize("damage", ["status", "type", "result_id", "result_project", "result_actor", "number"])
def test_inconsistent_receipt_is_not_acknowledged(world, damage):
    db, actor, project, other = world
    request = command(project)
    submit_defect_create(db, actor, request)
    db.commit()
    row = db.scalar(select(MobileSyncCommand))
    if damage == "status": row.status = "processing"
    elif damage == "type": row.entity_type = "work_log"
    else:
        key, value = {"result_id": ("id", row.entity_id + 1000), "result_project": ("project_id", other.id), "result_actor": ("author_user_id", actor.id + 1000), "number": ("number", 0)}[damage]
        row.result = {**row.result, key: value}
    db.commit()
    with pytest.raises(ValueError, match="offline_receipt_not_applied"):
        submit_defect_create(db, actor, request)
    assert count(db, Defect) == 1


def test_mutation_identity_cannot_switch_from_defect_to_work_log(world):
    db, actor, project, _ = world
    submit_defect_create(db, actor, command(project))
    db.commit()
    payload = envelope(project.id)
    payload.update(operation="work_log.create", data={"work_date": "2026-10-30", "work_name": "Synthetic journal work"})
    with pytest.raises(ValueError, match="client_mutation_id_reused"):
        submit_work_log_create(db, actor, OfflineWorkLogCreateRequest.model_validate(payload))
    assert count(db, WorkLogEntry) == 0 and count(db, Defect) == 1


@pytest.mark.parametrize("extra", ["user_id", "organization_id", "entity_id", "status"])
def test_envelope_forbids_client_owned_server_fields(extra):
    payload = envelope()
    payload[extra] = 1
    with pytest.raises(ValidationError): OfflineDefectCreateRequest.model_validate(payload)


@pytest.mark.parametrize("extra", ["photos", "rd_version_id", "created_by", "status"])
def test_data_does_not_accept_photos_actor_links_or_status(extra):
    payload = envelope()
    payload["data"][extra] = "synthetic"
    with pytest.raises(ValidationError): OfflineDefectCreateRequest.model_validate(payload)


@pytest.mark.parametrize("project", [True, 0, -1, "1", 1.5])
def test_project_is_a_strict_positive_integer(project):
    with pytest.raises(ValidationError): OfflineDefectCreateRequest.model_validate(envelope(project))


def test_project_id_at_safe_integer_boundary_is_accepted_unchanged():
    request = OfflineDefectCreateRequest.model_validate(envelope(MAX_SAFE_PROJECT_ID))
    assert request.project_id == MAX_SAFE_PROJECT_ID


@pytest.mark.parametrize("project", [MAX_SAFE_PROJECT_ID + 1, 10 ** 100])
def test_project_id_above_safe_integer_boundary_is_rejected_before_service(project):
    with pytest.raises(ValidationError):
        OfflineDefectCreateRequest.model_validate(envelope(project))


def test_client_timestamp_cannot_invent_a_timezone():
    payload = envelope()
    payload["client_created_at"] = "2026-10-30T09:00:00"
    with pytest.raises(ValidationError): OfflineDefectCreateRequest.model_validate(payload)


@pytest.mark.parametrize("field,limit", [("title",500),("description",5000),("object_name",300),("location",300),("responsible",300)])
def test_typed_text_caps_reject_loss_instead_of_silent_service_truncation(world, field, limit):
    db, actor, project, _ = world
    with pytest.raises(ValidationError):
        submit_defect_create(db, actor, command(project, **{field: "x" * (limit + 1)}))
    assert count(db, Defect) == count(db, MobileSyncCommand) == 0
    result = submit_defect_create(db, actor, command(project, **{field: "x" * limit}))
    assert result["result"][field] == "x" * limit


def test_title_cap_counts_unicode_characters_not_utf8_bytes(world):
    db, actor, project, _ = world
    title = "😀" * 500
    result = submit_defect_create(db, actor, command(project, title=title))
    assert result["result"]["title"] == title


def test_invalid_historical_numbering_does_not_leave_a_new_fact_or_receipt(world):
    db, actor, project, _ = world
    historical = Defect(project_id=project.id, number=-2, title="Synthetic invalid historical numbering")
    db.add(historical)
    db.commit()
    with pytest.raises(ValueError, match="offline_receipt_not_applied"):
        submit_defect_create(db, actor, command(project))
    assert count(db, Defect) == 1 and count(db, MobileSyncCommand) == 0
    assert db.get(Defect, historical.id).number == -2
