from datetime import date

from app.api.execution_finance import _schedule_cpm
from app.models.execution_finance import ScheduleItem


def _task(task_id, title, duration, predecessors=None, **values):
    return ScheduleItem(
        id=task_id, project_id=1, baseline_id=1, title=title, sort_order=task_id,
        duration_days=duration, predecessor_ids=predecessors,
        planned_progress=0, actual_progress=0, status="planned", **values,
    )


def test_backend_cpm_calculates_critical_path_and_float_for_typed_links():
    start = _task(1, "A", 3, planned_start=date(2026, 9, 1))
    critical = _task(2, "B", 2, "1FS")
    parallel = _task(3, "C", 2, "1SS+1d")
    milestone = _task(4, "D", 0, "2FF,3FF", is_milestone=True)

    result = _schedule_cpm([start, critical, parallel, milestone])

    assert result[1]["is_critical"] is True
    assert result[2]["is_critical"] is True
    assert result[4]["is_critical"] is True
    assert result[3]["total_float"] == 2
    assert result[3]["is_critical"] is False


def test_backend_cpm_supports_sf_and_upper_bound_constraints():
    predecessor = _task(1, "A", 2, planned_start=date(2026, 9, 1))
    sf_successor = _task(2, "B", 3, "1SF+2d")
    upper_bound = _task(
        3, "C", 1, planned_start=date(2026, 9, 5),
        constraint_type="snlt", constraint_date=date(2026, 9, 3),
    )
    finish_upper_bound = _task(
        4, "D", 2, planned_start=date(2026, 9, 5),
        constraint_type="fnlt", constraint_date=date(2026, 9, 4),
    )

    result = _schedule_cpm([predecessor, sf_successor, upper_bound, finish_upper_bound])

    assert result[2]["earliest_start"] == 0  # SF+2 with a three-day successor.
    assert result[3]["constraint_violation"] is True
    assert result[3]["total_float"] < 0
    assert result[3]["is_critical"] is True
    assert result[4]["constraint_violation"] is True
    assert result[4]["total_float"] < 0


def test_alap_uses_backward_pass_without_mutating_baseline_dates():
    driving = _task(1, "Driving", 5, planned_start=date(2026, 9, 1))
    alap = _task(2, "ALAP", 1, constraint_type="alap")

    result = _schedule_cpm([driving, alap])

    assert result[2]["earliest_start"] == 0
    assert result[2]["latest_start"] == 4
    assert result[2]["total_float"] == 4
    assert alap.planned_start is None and alap.planned_finish is None
