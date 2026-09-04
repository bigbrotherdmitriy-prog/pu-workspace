from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock


_JVM_LOCK = Lock()


class MppImportUnavailable(RuntimeError):
    pass


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


def _relation(relation) -> dict[str, str | None]:
    source = relation.getSourceTask()
    return {
        "external_uid": str(source.getUniqueID()) if source is not None else None,
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
        predecessors=[_relation(item) for item in task.getPredecessors()],
    )


def read_mpp_bytes(data: bytes) -> list[MppTask]:
    if not data:
        raise ValueError("MPP-файл пуст")
    try:
        import jpype
        import mpxj  # noqa: F401 - registers the packaged MPXJ jars
        with _JVM_LOCK:
            if not jpype.isJVMStarted():
                jpype.startJVM()
        from net.sf.mpxj.reader import UniversalProjectReader
    except Exception as exc:
        raise MppImportUnavailable("MPP parser is unavailable; MPXJ and Java 17 are required") from exc

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
