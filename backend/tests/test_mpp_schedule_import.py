import base64

import pytest
from fastapi import HTTPException

from app.api.execution_finance import MppImportRequest, _decode_mpp, _mpp_lag_suffix, import_mpp, router
from app.models.execution_finance import CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Organization
from app.models.project import Project
from app.schedule_import.mpp import MppImportUnavailable, _relation, map_mpxj_task
from app.schedule_import.mspdi import build_mspdi
from xml.etree import ElementTree


class Value:
    def __init__(self, value): self.value = value
    def __str__(self): return self.value


class Relation:
    def getSourceTask(self): return ValueTask(42)
    def getTargetTask(self): return ValueTask(17)
    def getType(self): return Value("FS")
    def getLag(self): return Value("0.0d")


class ValueTask:
    def __init__(self, uid): self.uid = uid
    def getUniqueID(self): return self.uid


class Task(ValueTask):
    def getID(self): return 12
    def getName(self): return "Монтаж оборудования"
    def getWBS(self): return "1.2.3"
    def getOutlineLevel(self): return 3
    def getParentTask(self): return ValueTask(9)
    def getStart(self): return Value("2026-09-01T08:00")
    def getFinish(self): return Value("2026-09-10T17:00")
    def getPercentageComplete(self): return 40
    def getDuration(self): return Value("8.0d")
    def getSummary(self): return False
    def getMilestone(self): return False
    def getCritical(self): return True
    def getCost(self): return Value("125400.50")
    def getPredecessors(self): return [Relation()]


def test_mpxj_task_preserves_hierarchy_dates_critical_path_and_dependencies():
    task = map_mpxj_task(Task(42))
    assert task.external_uid == "42"
    assert task.wbs == "1.2.3"
    assert task.outline_level == 3
    assert task.parent_external_uid == "9"
    assert task.planned_start.isoformat() == "2026-09-01"
    assert task.planned_finish.isoformat() == "2026-09-10"
    assert task.progress == 40
    assert task.duration_text == "8.0d"
    assert task.is_critical is True
    assert str(task.cost) == "125400.50"
    assert task.predecessors == [{"external_uid": "17", "type": "FS", "lag": "0.0d"}]


def test_mpxj_relation_uses_the_endpoint_other_than_the_current_task():
    current = ValueTask(42)

    class ReversedRelation(Relation):
        def getSourceTask(self): return ValueTask(17)
        def getTargetTask(self): return current

    assert _relation(Relation(), current)["external_uid"] == "17"
    assert _relation(ReversedRelation(), current)["external_uid"] == "17"


@pytest.mark.parametrize("relation_type", ["FS", "SS", "FF", "SF"])
def test_mpxj_modern_relation_preserves_predecessor_type_and_lag(relation_type):
    class ModernRelation:
        def getPredecessorTask(self): return ValueTask(17)
        def getSuccessorTask(self): return ValueTask(42)
        def getType(self): return Value(relation_type)
        def getLag(self): return Value("-2.0d")

    assert _relation(ModernRelation(), ValueTask(42)) == {
        "external_uid": "17", "type": relation_type, "lag": "-2.0d",
    }


@pytest.mark.parametrize("predecessor", [None, 42])
def test_mpxj_modern_relation_does_not_replace_missing_or_self_predecessor(predecessor):
    class ModernRelation(Relation):
        def getPredecessorTask(self):
            return ValueTask(predecessor) if predecessor is not None else None
        def getSourceTask(self): raise AssertionError("modern API must take priority")
        def getTargetTask(self): raise AssertionError("modern API must take priority")
        def getLag(self): return None

    assert _relation(ModernRelation(), ValueTask(42)) == {
        "external_uid": None, "type": "FS", "lag": None,
    }


def test_installed_mpxj_java_relation_maps_real_predecessor():
    from app.schedule_import.mpp import _universal_project_reader

    _universal_project_reader()  # Missing/broken production parser must fail, not skip.
    import jpype

    project = jpype.JClass("org.mpxj.ProjectFile")()
    predecessor = project.addTask()
    predecessor.setName("Подготовка")
    successor = project.addTask()
    successor.setName("Монтаж")
    successor.addPredecessor(
        jpype.JClass("org.mpxj.Relation$Builder")().predecessorTask(predecessor)
    )

    task = map_mpxj_task(successor)
    assert task.predecessors == [{
        "external_uid": str(predecessor.getUniqueID()), "type": "FS", "lag": "0.0d",
    }]
    assert task.predecessors[0]["external_uid"] != task.external_uid


def test_installed_mpxj_reader_imports_schedule_with_dependencies(tmp_path):
    from datetime import date
    from app.schedule_import.mpp import _universal_project_reader

    reader = _universal_project_reader()
    import jpype

    # Generate a non-confidential fixture with the installed Java writer, then
    # exercise the real reader + mapper used by binary MPP preview/import.
    project = jpype.JClass("org.mpxj.ProjectFile")()
    predecessor = project.addTask()
    predecessor.setName("Подготовка")
    successor = project.addTask()
    successor.setName("Монтаж")
    successor.setStart(jpype.JClass("java.time.LocalDateTime").of(2026, 9, 5, 8, 0))
    successor.addPredecessor(
        jpype.JClass("org.mpxj.Relation$Builder")().predecessorTask(predecessor).lag(
            jpype.JClass("org.mpxj.Duration").getInstance(
                jpype.JDouble(2), jpype.JClass("org.mpxj.TimeUnit").DAYS,
            )
        )
    )
    source = tmp_path / "regression.xml"
    jpype.JClass("org.mpxj.mspdi.MSPDIWriter")().write(project, str(source))
    project = reader().read(str(source))
    tasks = {str(task.getName()): map_mpxj_task(task) for task in project.getTasks()
             if task.getName() is not None}
    assert tasks["Монтаж"].planned_start == date(2026, 9, 5)
    assert tasks["Монтаж"].predecessors == [{
        "external_uid": tasks["Подготовка"].external_uid, "type": "FS", "lag": "2.0d",
    }]


def test_mpxj_reader_prefers_the_current_org_namespace(monkeypatch):
    import app.schedule_import.mpp as mpp_module

    calls = []

    class FakeJpype:
        @staticmethod
        def isJVMStarted(): return True

        @staticmethod
        def JClass(name):
            calls.append(name)
            if name == "org.mpxj.reader.UniversalProjectReader":
                return "current-reader"
            raise AssertionError("legacy namespace must not be consulted")

    monkeypatch.setitem(__import__("sys").modules, "jpype", FakeJpype)
    monkeypatch.setitem(__import__("sys").modules, "mpxj", object())

    assert mpp_module._universal_project_reader() == "current-reader"
    assert calls == ["org.mpxj.reader.UniversalProjectReader"]


def test_mpxj_reader_falls_back_to_the_legacy_namespace(monkeypatch):
    import app.schedule_import.mpp as mpp_module

    calls = []

    class FakeJpype:
        @staticmethod
        def isJVMStarted(): return True

        @staticmethod
        def JClass(name):
            calls.append(name)
            if name == "net.sf.mpxj.reader.UniversalProjectReader":
                return "legacy-reader"
            raise TypeError("class not found")

    monkeypatch.setitem(__import__("sys").modules, "jpype", FakeJpype)
    monkeypatch.setitem(__import__("sys").modules, "mpxj", object())

    assert mpp_module._universal_project_reader() == "legacy-reader"
    assert calls == [
        "org.mpxj.reader.UniversalProjectReader",
        "net.sf.mpxj.reader.UniversalProjectReader",
    ]


def test_mpxj_reader_reports_an_actionable_error_when_no_namespace_exists(monkeypatch):
    import app.schedule_import.mpp as mpp_module

    class FakeJpype:
        @staticmethod
        def isJVMStarted(): return True

        @staticmethod
        def JClass(_name): raise TypeError("class not found")

    monkeypatch.setitem(__import__("sys").modules, "jpype", FakeJpype)
    monkeypatch.setitem(__import__("sys").modules, "mpxj", object())

    with pytest.raises(MppImportUnavailable, match=r"Java 17\+"):
        mpp_module._universal_project_reader()


def test_mpp_routes_and_binary_validation_are_explicit():
    paths = {route.path for route in router.routes}
    assert "/execution/mpp/preview" in paths
    assert "/execution/mpp/import" in paths
    assert "/execution/mpp/export/{baseline_id}" in paths
    data, digest = _decode_mpp(MppImportRequest(project_id=1, filename="plan.mpp", content_base64="TVBQ"))
    assert data == b"MPP"
    assert len(digest) == 64


def test_mpp_lag_is_normalized_for_native_gpr_dependencies():
    assert _mpp_lag_suffix("0.0d") == ""
    assert _mpp_lag_suffix("2.0d") == "+2d"
    assert _mpp_lag_suffix("-1.0d") == "-1d"
    assert _mpp_lag_suffix("2.5h") == ""


def test_mpp_costs_create_idempotent_cash_flow_proposals_only_after_opt_in(
    db_session, user_factory, monkeypatch,
):
    user = user_factory(is_admin=True)
    organization = Organization(name="MPP cost organization")
    db_session.add(organization); db_session.flush()
    project = Project(name="MPP cost project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    task = map_mpxj_task(Task(42))
    monkeypatch.setattr("app.api.execution_finance._mpp_tasks", lambda _data: [task])
    payload = MppImportRequest(
        project_id=project.id,
        filename="plan.mpp",
        content_base64=base64.b64encode(b"MPP with costs").decode(),
        create_cash_flow_proposals=True,
        cash_flow_currency="RUB",
    )

    result = import_mpp(payload, db_session, user)

    assert result["cash_flow_proposals_created"] == 1
    assert db_session.query(ScheduleBaseline).count() == 1
    schedule = db_session.query(ScheduleItem).one()
    proposal = db_session.query(CashFlowEntry).one()
    assert proposal.schedule_item_id == schedule.id
    assert proposal.planned_amount == 125400.50
    assert proposal.planned_date.isoformat() == "2026-09-10"
    assert proposal.status == "proposed"
    assert proposal.direction == "outflow"

    replay = import_mpp(payload, db_session, user)
    assert replay["duplicate"] is True
    assert replay["cash_flow_proposals_created"] == 0
    assert db_session.query(CashFlowEntry).count() == 1


def test_mpp_preview_shows_row_level_direction_instead_of_a_hidden_constant(
    db_session, user_factory, monkeypatch,
):
    """ADR-V6-05-INCOME-BUDGET-RU, step 8в: an .mpp file carries no direction
    signal; the default stays outflow, but it must be visible per task in
    preview and overridable, not an invisible constant."""
    user = user_factory(is_admin=True)
    organization = Organization(name="MPP direction preview organization")
    db_session.add(organization); db_session.flush()
    project = Project(name="MPP direction preview project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    task = map_mpxj_task(Task(42))
    monkeypatch.setattr("app.api.execution_finance._mpp_tasks", lambda _data: [task])
    from app.api.execution_finance import preview_mpp

    defaulted = preview_mpp(MppImportRequest(
        project_id=project.id, filename="plan.mpp",
        content_base64=base64.b64encode(b"MPP with costs").decode(),
    ), db_session, user)
    assert defaulted["cost_rows"] == [{
        "external_uid": "42", "title": "Монтаж оборудования",
        "cost": 125400.50, "planned_date": defaulted["cost_rows"][0]["planned_date"],
        "direction": "outflow", "direction_defaulted": True,
    }]

    overridden = preview_mpp(MppImportRequest(
        project_id=project.id, filename="plan.mpp",
        content_base64=base64.b64encode(b"MPP with costs").decode(),
        direction_overrides={"42": "inflow"},
    ), db_session, user)
    assert overridden["cost_rows"][0]["direction"] == "inflow"
    assert overridden["cost_rows"][0]["direction_defaulted"] is False


def test_mpp_preview_rejects_direction_override_for_an_unknown_task(
    db_session, user_factory, monkeypatch,
):
    user = user_factory(is_admin=True)
    organization = Organization(name="MPP unknown override organization")
    db_session.add(organization); db_session.flush()
    project = Project(name="MPP unknown override project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    task = map_mpxj_task(Task(42))
    monkeypatch.setattr("app.api.execution_finance._mpp_tasks", lambda _data: [task])
    from app.api.execution_finance import preview_mpp

    with pytest.raises(HTTPException) as error:
        preview_mpp(MppImportRequest(
            project_id=project.id, filename="plan.mpp",
            content_base64=base64.b64encode(b"MPP with costs").decode(),
            direction_overrides={"999": "inflow"},
        ), db_session, user)
    assert error.value.status_code == 422


def test_mpp_import_applies_a_direction_override_instead_of_the_default(
    db_session, user_factory, monkeypatch,
):
    user = user_factory(is_admin=True)
    organization = Organization(name="MPP override import organization")
    db_session.add(organization); db_session.flush()
    project = Project(name="MPP override import project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    task = map_mpxj_task(Task(42))
    monkeypatch.setattr("app.api.execution_finance._mpp_tasks", lambda _data: [task])
    payload = MppImportRequest(
        project_id=project.id, filename="plan.mpp",
        content_base64=base64.b64encode(b"MPP with costs").decode(),
        create_cash_flow_proposals=True, cash_flow_currency="RUB",
        direction_overrides={"42": "inflow"},
    )

    result = import_mpp(payload, db_session, user)

    assert result["cash_flow_proposals_created"] == 1
    proposal = db_session.query(CashFlowEntry).one()
    assert proposal.direction == "inflow"
    assert "по умолчанию" not in proposal.note


def test_mpp_import_rejects_a_direction_override_for_an_unknown_task(
    db_session, user_factory, monkeypatch,
):
    user = user_factory(is_admin=True)
    organization = Organization(name="MPP import unknown override organization")
    db_session.add(organization); db_session.flush()
    project = Project(name="MPP import unknown override project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    task = map_mpxj_task(Task(42))
    monkeypatch.setattr("app.api.execution_finance._mpp_tasks", lambda _data: [task])
    payload = MppImportRequest(
        project_id=project.id, filename="plan.mpp",
        content_base64=base64.b64encode(b"MPP with costs").decode(),
        create_cash_flow_proposals=True, direction_overrides={"999": "inflow"},
    )

    with pytest.raises(HTTPException) as error:
        import_mpp(payload, db_session, user)
    assert error.value.status_code == 422


def test_mspdi_export_preserves_hierarchy_progress_and_dependency():
    data = build_mspdi("ГПР", [
        {"id": 10, "parent_id": None, "title": "Этап", "duration_days": 5, "actual_progress": 40},
        {"id": 20, "parent_id": 10, "title": "Работа", "duration_days": 2, "actual_progress": 20,
         "planned_start": __import__("datetime").date(2026, 9, 1), "planned_finish": __import__("datetime").date(2026, 9, 2),
         "predecessor_ids": "10FS+2d"},
    ])
    root = ElementTree.fromstring(data)
    ns = {"p": "http://schemas.microsoft.com/project"}
    tasks = root.findall("p:Tasks/p:Task", ns)
    assert [task.findtext("p:OutlineLevel", namespaces=ns) for task in tasks] == ["1", "2"]
    assert tasks[1].findtext("p:PercentComplete", namespaces=ns) == "20"
    assert tasks[1].findtext("p:PredecessorLink/p:PredecessorUID", namespaces=ns) == "1"
