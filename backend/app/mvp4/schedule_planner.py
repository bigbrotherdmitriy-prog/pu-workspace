"""Pure calendar-day planning, adapted from the existing GPR graph utilities.

There is no database, clock, provider, authorization or baseline mutation here.
The caller supplies one complete, authorized draft graph and an explicit project
start. Dates are inclusive: an N-day activity ends N-1 days after its start;
a zero-duration milestone is a point with start == finish. For both, FS means
successor start >= predecessor finish + 1 calendar day + lag. SS/FF/SF compare
their named dates directly plus lag. Working calendars/resources are unsupported.

planned_start is a not-before floor, not a fixed date. Explicit MSO/MFO constraints
fix dates; infeasible constraints fail instead of overriding dependencies. ALAP is
unsupported and is rejected. Critical IDs mean zero constrained total float; there
may be multiple critical branches or fixed-date isolated tasks, not one path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
import heapq
import re


class PlannerError(ValueError):
    """Stable, content-free rejection for the API integrator to translate."""

    def __init__(self, code: str, task_ids: tuple[int, ...] = ()):
        self.code = code
        self.task_ids = tuple(sorted(set(task_ids)))
        super().__init__(code)


@dataclass(frozen=True)
class Dependency:
    predecessor_id: int
    link_type: str = "FS"
    lag_days: int = 0


@dataclass(frozen=True)
class ScheduleTask:
    task_id: int
    duration_days: int
    dependencies: tuple[Dependency, ...] = ()
    planned_start: date | None = None
    is_milestone: bool = False
    constraint_type: str = "asap"
    constraint_date: date | None = None


@dataclass(frozen=True)
class TaskPlan:
    task_id: int
    earliest_start: date
    earliest_finish: date
    latest_start: date
    latest_finish: date
    total_float_days: int
    free_float_days: int


@dataclass(frozen=True)
class SchedulePlan:
    project_start: date
    project_finish: date | None
    tasks: tuple[TaskPlan, ...]
    topological_order: tuple[int, ...]
    critical_ids: tuple[int, ...]
    critical_edges: tuple[tuple[int, int], ...]


_LINK_TYPES = frozenset({"FS", "SS", "FF", "SF"})
_CONSTRAINTS = frozenset({"asap", "snet", "fnet", "snlt", "fnlt", "mso", "mfo"})
_DEPENDENCY_TOKEN = re.compile(r"\s*([0-9]+)\s*(FS|SS|FF|SF)?\s*(?:([+-])\s*([0-9]+)\s*[dд])?\s*", re.IGNORECASE)


def parse_dependencies(value: str | None) -> tuple[Dependency, ...]:
    """Parse the existing '12FS+2d; 7SS-1д' format; never ignore a bad token."""
    if value is None:
        return ()
    if not isinstance(value, str) or len(value) > 2000:
        raise PlannerError("invalid_dependency_syntax")
    if not value.strip():
        return ()
    dependencies = []
    seen = set()
    for token in re.split(r"[,;]", value):
        match = _DEPENDENCY_TOKEN.fullmatch(token)
        if not match or int(match[1]) < 1:
            raise PlannerError("invalid_dependency_syntax")
        predecessor_id = int(match[1])
        if predecessor_id in seen:
            raise PlannerError("duplicate_dependency", (predecessor_id,))
        seen.add(predecessor_id)
        lag = int(match[4] or 0) * (-1 if match[3] == "-" else 1)
        dependencies.append(Dependency(predecessor_id, (match[2] or "FS").upper(), lag))
    return tuple(sorted(dependencies, key=lambda dependency: dependency.predecessor_id))


def _validate_date(value: date | None, *, optional: bool = False) -> None:
    if not (optional and value is None) and type(value) is not date:
        raise PlannerError("invalid_date")


def _validate_task(task: ScheduleTask) -> None:
    if not isinstance(task, ScheduleTask):
        raise PlannerError("invalid_task")
    if type(task.task_id) is not int or task.task_id < 1:
        raise PlannerError("invalid_task_id")
    if (type(task.is_milestone) is not bool or type(task.duration_days) is not int
            or (task.is_milestone and task.duration_days != 0)
            or (not task.is_milestone and not 1 <= task.duration_days <= 10000)):
        raise PlannerError("invalid_duration", (task.task_id,))
    _validate_date(task.planned_start, optional=True)
    _validate_date(task.constraint_date, optional=True)
    if not isinstance(task.constraint_type, str) or task.constraint_type not in _CONSTRAINTS:
        raise PlannerError("unsupported_constraint", (task.task_id,))
    if (task.constraint_type == "asap") != (task.constraint_date is None):
        raise PlannerError("invalid_constraint_date", (task.task_id,))
    if not isinstance(task.dependencies, tuple):
        raise PlannerError("invalid_dependencies", (task.task_id,))
    for dependency in task.dependencies:
        if (not isinstance(dependency, Dependency)
                or type(dependency.predecessor_id) is not int or dependency.predecessor_id < 1
                or not isinstance(dependency.link_type, str) or dependency.link_type not in _LINK_TYPES
                or type(dependency.lag_days) is not int):
            raise PlannerError("invalid_dependency", (task.task_id,))


def _span(task: ScheduleTask) -> int:
    return 0 if task.is_milestone else task.duration_days - 1


def _offset(predecessor: ScheduleTask, successor: ScheduleTask, link: Dependency) -> int:
    """Convert the named-date inequality to successor start >= predecessor start + offset."""
    if link.link_type == "FS":
        return _span(predecessor) + 1 + link.lag_days
    if link.link_type == "SS":
        return link.lag_days
    if link.link_type == "FF":
        return _span(predecessor) - _span(successor) + link.lag_days
    return -_span(successor) + link.lag_days  # SF


def _bounds(task: ScheduleTask, origin: int) -> tuple[int, int | None]:
    lower = max(0, task.planned_start.toordinal() - origin) if task.planned_start else 0
    upper = None
    if task.constraint_date is not None:
        bound = task.constraint_date.toordinal() - origin
        if task.constraint_type in {"fnet", "fnlt", "mfo"}:
            bound -= _span(task)
        if task.constraint_type in {"snet", "fnet", "mso", "mfo"}:
            lower = max(lower, bound)
        if task.constraint_type in {"snlt", "fnlt", "mso", "mfo"}:
            upper = bound
    return lower, upper


def _calendar_date(origin: int, offset: int, task_id: int) -> date:
    ordinal = origin + offset
    if not date.min.toordinal() <= ordinal <= date.max.toordinal():
        raise PlannerError("date_out_of_range", (task_id,))
    return date.fromordinal(ordinal)


def plan_schedule(tasks: Iterable[ScheduleTask], *, project_start: date) -> SchedulePlan:
    """Validate a complete DAG and return deterministic dates/float without side effects.

    The date anchor applies to every task, including negative-lag successors.
    Normal durations must be 1..10000 days; milestones must have duration zero.
    Missing tasks/cycles/malformed input and inconsistent date windows return no
    partial plan. Callers must not interpret a returned plan as write authority.
    """
    _validate_date(project_start)
    by_id = {}
    for task in tasks:
        _validate_task(task)
        if task.task_id in by_id:
            raise PlannerError("duplicate_task", (task.task_id,))
        by_id[task.task_id] = task
    ids = sorted(by_id)
    incoming: dict[int, list[tuple[int, int]]] = {task_id: [] for task_id in ids}
    outgoing: dict[int, list[tuple[int, int]]] = {task_id: [] for task_id in ids}
    for task_id in ids:
        task = by_id[task_id]
        seen = set()
        for dependency in sorted(task.dependencies, key=lambda link: link.predecessor_id):
            predecessor_id = dependency.predecessor_id
            if predecessor_id not in by_id:
                raise PlannerError("unknown_dependency", (task_id, predecessor_id))
            if predecessor_id == task_id:
                raise PlannerError("self_dependency", (task_id,))
            if predecessor_id in seen:
                raise PlannerError("duplicate_dependency", (task_id, predecessor_id))
            seen.add(predecessor_id)
            offset = _offset(by_id[predecessor_id], task, dependency)
            incoming[task_id].append((predecessor_id, offset))
            outgoing[predecessor_id].append((task_id, offset))

    degrees = {task_id: len(incoming[task_id]) for task_id in ids}
    ready = [task_id for task_id in ids if degrees[task_id] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        task_id = heapq.heappop(ready)
        ordered.append(task_id)
        for successor_id, _offset_days in outgoing[task_id]:
            degrees[successor_id] -= 1
            if degrees[successor_id] == 0:
                heapq.heappush(ready, successor_id)
    if len(ordered) != len(ids):
        raise PlannerError("cycle", tuple(task_id for task_id in ids if degrees[task_id]))
    if not ordered:
        return SchedulePlan(project_start, None, (), (), (), ())

    origin = project_start.toordinal()
    earliest: dict[int, int] = {}
    upper_bounds: dict[int, int | None] = {}
    for task_id in ordered:
        lower, upper = _bounds(by_id[task_id], origin)
        start = max([lower, *(earliest[pred] + offset for pred, offset in incoming[task_id])])
        if upper is not None and start > upper:
            raise PlannerError("infeasible_constraint", (task_id,))
        _calendar_date(origin, start + _span(by_id[task_id]), task_id)
        earliest[task_id] = start
        upper_bounds[task_id] = upper

    horizon = max(earliest[task_id] + _span(by_id[task_id]) for task_id in ids)
    latest: dict[int, int] = {}
    for task_id in reversed(ordered):
        start = horizon - _span(by_id[task_id])
        if upper_bounds[task_id] is not None:
            start = min(start, upper_bounds[task_id])
        latest[task_id] = min([start, *(latest[succ] - offset for succ, offset in outgoing[task_id])])
        if latest[task_id] < earliest[task_id]:
            raise PlannerError("infeasible_constraint", (task_id,))

    plans = []
    for task_id in ordered:
        start, span = earliest[task_id], _span(by_id[task_id])
        free_limits = [horizon - start - span]
        if upper_bounds[task_id] is not None:
            free_limits.append(upper_bounds[task_id] - start)
        free_limits.extend(earliest[succ] - start - offset for succ, offset in outgoing[task_id])
        plans.append(TaskPlan(
            task_id, _calendar_date(origin, start, task_id), _calendar_date(origin, start + span, task_id),
            _calendar_date(origin, latest[task_id], task_id), _calendar_date(origin, latest[task_id] + span, task_id),
            latest[task_id] - start, min(free_limits),
        ))
    critical_ids = tuple(task_id for task_id in ids if latest[task_id] == earliest[task_id])
    critical = set(critical_ids)
    critical_edges = tuple(sorted(
        (predecessor, successor) for predecessor in critical_ids for successor, offset in outgoing[predecessor]
        if successor in critical and earliest[successor] == earliest[predecessor] + offset
    ))
    return SchedulePlan(project_start, _calendar_date(origin, horizon, ordered[-1]), tuple(plans),
                        tuple(ordered), critical_ids, critical_edges)
