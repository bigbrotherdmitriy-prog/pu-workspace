from app.api.execution_finance import MppImportRequest, _decode_mpp, _mpp_lag_suffix, router
from app.schedule_import.mpp import map_mpxj_task


class Value:
    def __init__(self, value): self.value = value
    def __str__(self): return self.value


class Relation:
    def getSourceTask(self): return ValueTask(17)
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
    assert task.predecessors == [{"external_uid": "17", "type": "FS", "lag": "0.0d"}]


def test_mpp_routes_and_binary_validation_are_explicit():
    paths = {route.path for route in router.routes}
    assert "/execution/mpp/preview" in paths
    assert "/execution/mpp/import" in paths
    data, digest = _decode_mpp(MppImportRequest(project_id=1, filename="plan.mpp", content_base64="TVBQ"))
    assert data == b"MPP"
    assert len(digest) == 64


def test_mpp_lag_is_normalized_for_native_gpr_dependencies():
    assert _mpp_lag_suffix("0.0d") == ""
    assert _mpp_lag_suffix("2.0d") == "+2d"
    assert _mpp_lag_suffix("-1.0d") == "-1d"
    assert _mpp_lag_suffix("2.5h") == ""
