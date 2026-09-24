from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock


_JVM_LOCK = Lock()
_READER_CLASS_NAMES = (
    "org.mpxj.reader.UniversalProjectReader",
    "net.sf.mpxj.reader.UniversalProjectReader",
)


class MppImportUnavailable(RuntimeError):
    pass


def _universal_project_reader():
    """Load the reader across the MPXJ 16 and legacy Java namespaces."""
    try:
        import jpype
        import mpxj  # noqa: F401 - registers the packaged MPXJ jars

        with _JVM_LOCK:
            if not jpype.isJVMStarted():
                jpype.startJVM()

        last_error = None
        for class_name in _READER_CLASS_NAMES:
            try:
                return jpype.JClass(class_name)
            except Exception as exc:  # JPype uses Java-specific lookup errors.
                last_error = exc
        raise last_error or ImportError("MPXJ reader class was not found")
    except Exception as exc:
        raise MppImportUnavailable(
            "MPP parser could not be initialized; compatible MPXJ and Java 17+ are required"
        ) from exc


@dataclass(frozen=True)
class MppTask:
    external_uid: str
    task_id: int
    title: str
    wbs: str | None
    outline_level: int
    parent_external_uid: str | None
    planned_start: date | None
    planned_finish: date | None
    progress: float
    duration_text: str | None
    is_summary: bool
    is_milestone: bool
    is_critical: bool
    cost: Decimal | None
    predecessors: list[dict[str, str | None]]

    def to_dict(self) -> dict:
        return asdict(self)


def _date(value) -> date | None:
    if value is None:
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _task_uid(task) -> str | None:
    return str(task.getUniqueID()) if task is not None else None


def _cost(task) -> Decimal | None:
    getter = getattr(task, "getCost", None)
    value = getter() if getter else None
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _relation(relation, current_task) -> dict[str, str | None]:
    """Return the task at the other end of a predecessor relation.

    MPXJ exposes a relation from the task currently being inspected to its
    predecessor.  Using ``getSourceTask`` unconditionally therefore turns
    every imported dependency into a self-reference for affected MPP files.
    Picking the endpoint which is not the current task is stable across MPXJ
    relation orientations and also keeps the adapter easy to fake in tests.
    """
    current_uid = _task_uid(current_task)
    source = relation.getSourceTask()
    target = relation.getTargetTask()
    source_uid = _task_uid(source)
    target_uid = _task_uid(target)
    predecessor_uid = target_uid if source_uid == current_uid else source_uid
    if predecessor_uid == current_uid:
        predecessor_uid = None
    return {
        "external_uid": predecessor_uid,
        "type": str(relation.getType()),
        "lag": str(relation.getLag()) if relation.getLag() is not None else None,
    }


def map_mpxj_task(task) -> MppTask:
    parent = task.getParentTask()
    return MppTask(
        external_uid=str(task.getUniqueID()),
        task_id=int(task.getID()),
        title=str(task.getName()),
        wbs=str(task.getWBS()) if task.getWBS() is not None else None,
        outline_level=int(task.getOutlineLevel() or 0),
        parent_external_uid=str(parent.getUniqueID()) if parent is not None else None,
        planned_start=_date(task.getStart()),
        planned_finish=_date(task.getFinish()),
        progress=float(task.getPercentageComplete() or 0),
        duration_text=str(task.getDuration()) if task.getDuration() is not None else None,
        is_summary=bool(task.getSummary()),
        is_milestone=bool(task.getMilestone()),
        is_critical=bool(task.getCritical()),
        cost=_cost(task),
        predecessors=[_relation(item, task) for item in task.getPredecessors()],
    )


def read_mpp_bytes(data: bytes) -> list[MppTask]:
    if not data:
        raise ValueError("MPP-файл пуст")
    UniversalProjectReader = _universal_project_reader()

    with TemporaryDirectory(prefix="pu-mpp-") as temp_dir:
        source = Path(temp_dir) / "schedule.mpp"
        source.write_bytes(data)
        try:
            project = UniversalProjectReader().read(str(source))
        except Exception as exc:
            raise ValueError("Не удалось прочитать MPP-файл") from exc

    result: list[MppTask] = []
    for task in project.getTasks():
        if task.getName() is None:
            continue
        result.append(map_mpxj_task(task))
    if not result:
        raise ValueError("В MPP-файле не найдены задачи")
    return result
