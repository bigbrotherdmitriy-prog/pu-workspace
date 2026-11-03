"""Pure owner precondition + real synthetic service composition, no route calls."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.mobile_create_owner import require_create_owner
from app.mobile_defect import submit_defect_create
from app.mobile_work_log import submit_work_log_create
from app.models.defects import Defect
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.work_log import WorkLogEntry
from test_mobile_defect_create import command as defect_command
from test_mobile_work_log_service import world, count, command as work_command


@pytest.mark.parametrize("kind", ["defect", "work_log"])
def test_changed_session_is_refused_before_any_business_creation(world, kind):
    db, original, project, _ = world
    actual = User(name="Synthetic changed session", email="changed-owner@example.invalid", is_admin=False)
    db.add(actual)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=actual.id, role="editor"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        require_create_owner(actual, original.id)
        if kind == "defect": submit_defect_create(db, actual, defect_command(project))
        else: submit_work_log_create(db, actual, work_command(project))
    assert exc.value.status_code == 403
    assert count(db, Defect) == count(db, WorkLogEntry) == 0


def test_matching_precondition_does_not_replace_the_authenticated_author(world):
    db, actor, project, _ = world
    require_create_owner(actor, actor.id)
    result = submit_defect_create(db, actor, defect_command(project))
    assert db.get(Defect, result["entity_id"]).created_by == actor.id


@pytest.mark.parametrize("unparsed", [None, True, 0, "1"])
def test_helper_rejects_missing_or_unparsed_owner(unparsed):
    with pytest.raises(HTTPException) as exc:
        require_create_owner(SimpleNamespace(id=1), unparsed)
    assert exc.value.status_code == 422


def test_absent_authenticated_actor_cannot_be_supplied_by_the_precondition():
    with pytest.raises(HTTPException) as exc:
        require_create_owner(None, 1)
    assert exc.value.status_code == 401
