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


def test_queued_upload_declares_owner_precondition_before_decode_or_staging():
    """Static binding proof only; HTTP parsing/authorization remains UNRUN."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'app/api/local_upload.py').read_text())
    route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'analyze_local_folder')
    defaults = dict(zip([arg.arg for arg in route.args.args][-len(route.args.defaults):], route.args.defaults))
    header = defaults['expected_user_id']
    assert isinstance(header, ast.Call) and isinstance(header.func, ast.Name) and header.func.id == 'Header'
    keywords = {kw.arg: kw.value for kw in header.keywords}
    assert isinstance(keywords['default'], ast.Constant) and keywords['default'].value is None
    assert isinstance(keywords['alias'], ast.Constant) and keywords['alias'].value == 'X-PU-Expected-User'
    calls = [node for node in ast.walk(route) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    precondition = next(node for node in calls if node.func.id == 'require_create_owner')
    assert [arg.id for arg in precondition.args] == ['user', 'expected_user_id']
    assert precondition.lineno < min(node.lineno for node in calls if node.func.id in {'get_local_upload_runtime', 'decode_local_file', 'stage_and_enqueue'})
