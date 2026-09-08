"""Synthetic calendar-day graph contracts; no DB, provider or current-time input."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
from itertools import product

import pytest

from app.mvp4.schedule_planner import (
    Dependency,
    PlannerError,
    ScheduleTask,
    parse_dependencies,
    plan_schedule,
)


START = date(2026, 1, 1)


def day(offset):
    return START + timedelta(days=offset)


def task(task_id, duration=1, predecessors="", start=None, milestone=False,
         constraint="asap", constraint_day=None):
    return ScheduleTask(task_id=task_id, duration_days=duration,
                        dependencies=parse_dependencies(predecessors), is_milestone=milestone,
                        planned_start=day(start) if start is not None else None,
                        constraint_type=constraint,
                        constraint_date=day(constraint_day) if constraint_day is not None else None)


def rows(result):
    return {row.task_id: row for row in result.tasks}


def assert_error(code, tasks, **kwargs):
    with pytest.raises(PlannerError) as error:
        plan_schedule(tasks, project_start=kwargs.get("project_start", START))
    assert error.value.code == code


def test_parse_reuses_project_style_types_signed_calendar_lags_and_default_fs():
    assert parse_dependencies(" 3 sf - 2 д ; 1; 2SS + 3d ") == (
        Dependency(1, "FS", 0), Dependency(2, "SS", 3), Dependency(3, "SF", -2),
    )
    assert parse_dependencies(None) == parse_dependencies("  ") == ()


@pytest.mark.parametrize("value", ["0FS", "-1", "1XX", "1FS+2", "1FS+2weeks", "1,,2", "1;",
                                    "1FS;1SS", "one", "1" * 2001])
def test_malformed_or_duplicate_dependency_text_is_not_silently_dropped(value):
    with pytest.raises(PlannerError):
        parse_dependencies(value)


@pytest.mark.parametrize("kind", ["FS", "SS", "FF", "SF"])
@pytest.mark.parametrize("lag", [-2, 0, 2])
@pytest.mark.parametrize("pred_milestone,succ_milestone", product([False, True], repeat=2))
def test_all_link_types_and_lags_have_one_inclusive_date_semantics(kind, lag, pred_milestone, succ_milestone):
    pred_duration = 0 if pred_milestone else 4
    succ_duration = 0 if succ_milestone else 3
    result = rows(plan_schedule([
        task(1, pred_duration, start=10, milestone=pred_milestone),
        task(2, succ_duration, f"1{kind}{lag:+}d", milestone=succ_milestone),
    ], project_start=START))
    predecessor, successor = result[1], result[2]
    if kind == "FS":
        expected = predecessor.earliest_finish + timedelta(days=1 + lag)
        assert successor.earliest_start == expected
    elif kind == "SS":
        assert successor.earliest_start == predecessor.earliest_start + timedelta(days=lag)
    elif kind == "FF":
        assert successor.earliest_finish == predecessor.earliest_finish + timedelta(days=lag)
    else:
        assert successor.earliest_finish == predecessor.earliest_start + timedelta(days=lag)
    assert (successor.earliest_finish - successor.earliest_start).days == max(0, succ_duration - 1)


def test_chain_dates_float_and_tight_critical_edges():
    result = plan_schedule([task(1, 3), task(2, 2, "1"), task(3, 1, "2")], project_start=START)
    assert result.project_finish == day(5)
    assert result.topological_order == result.critical_ids == (1, 2, 3)
    assert result.critical_edges == ((1, 2), (2, 3))
    assert [(row.earliest_start, row.earliest_finish) for row in result.tasks] == [
        (day(0), day(2)), (day(3), day(4)), (day(5), day(5)),
    ]
    assert all(row.total_float_days == row.free_float_days == 0 for row in result.tasks)


def test_diamond_reports_short_branch_float_not_a_second_critical_path():
    result = plan_schedule([task(1, 2), task(2, 4, "1"), task(3, 2, "1"), task(4, 1, "2;3")], project_start=START)
    indexed = rows(result)
    assert result.project_finish == day(6)
    assert result.critical_ids == (1, 2, 4)
    assert result.critical_edges == ((1, 2), (2, 4))
    assert indexed[3].total_float_days == indexed[3].free_float_days == 2
    assert indexed[3].latest_start == day(4)


def test_equal_diamond_preserves_both_critical_branches():
    result = plan_schedule([task(1, 2), task(2, 4, "1"), task(3, 4, "1"), task(4, 1, "2;3")], project_start=START)
    assert result.critical_ids == (1, 2, 3, 4)
    assert result.critical_edges == ((1, 2), (1, 3), (2, 4), (3, 4))


def test_real_planned_start_floor_is_included_in_float_calculation():
    result = plan_schedule([task(1), task(2, predecessors="1", start=10)], project_start=START)
    indexed = rows(result)
    assert indexed[1].total_float_days == indexed[1].free_float_days == 9
    assert indexed[1].latest_start == day(9)
    assert result.critical_ids == (2,)
    assert result.critical_edges == ()


def test_disconnected_short_task_has_float_to_common_project_finish():
    result = rows(plan_schedule([task(1, 5), task(2, 2)], project_start=START))
    assert result[2].total_float_days == result[2].free_float_days == 3


def test_negative_lag_never_moves_before_explicit_project_start():
    result = rows(plan_schedule([task(1, 3), task(2, predecessors="1FS-10d")], project_start=START))
    assert result[2].earliest_start == START


@pytest.mark.parametrize("kind, expected_earliest, expected_latest", [
    ("snet", 5, 7), ("fnet", 3, 7), ("mso", 5, 5), ("mfo", 3, 3),
    ("snlt", 0, 5), ("fnlt", 0, 3),
])
def test_supported_constraints_apply_to_forward_and_backward_passes(kind, expected_earliest, expected_latest):
    result = rows(plan_schedule([task(1, 10), task(2, 3, constraint=kind, constraint_day=5)], project_start=START))
    assert result[2].earliest_start == day(expected_earliest)
    assert result[2].latest_start == day(expected_latest)
    assert result[2].total_float_days == result[2].free_float_days == expected_latest - expected_earliest


@pytest.mark.parametrize("kind, constraint_day", [("mso", 2), ("mfo", 4), ("snlt", 2), ("fnlt", 4)])
def test_infeasible_constraint_cannot_override_dependency_or_be_ignored(kind, constraint_day):
    assert_error("infeasible_constraint", [task(1, 4), task(2, 3, "1", constraint=kind, constraint_day=constraint_day)])


@pytest.mark.parametrize("kind", ["alap", "unknown"])
def test_unsupported_constraints_fail_explicitly(kind):
    assert_error("unsupported_constraint", [task(1, constraint=kind)])


@pytest.mark.parametrize("spec", [task(1, constraint="mso"), task(1, constraint="asap", constraint_day=1)])
def test_constraint_date_is_neither_missing_nor_silently_ignored(spec):
    assert_error("invalid_constraint_date", [spec])


def test_unknown_self_duplicate_dependencies_and_duplicate_ids_are_errors():
    assert_error("unknown_dependency", [task(1, predecessors="2")])
    assert_error("self_dependency", [task(1, predecessors="1")])
    assert_error("duplicate_task", [task(1), task(1)])
    assert_error("duplicate_dependency", [task(1), replace(task(2), dependencies=(Dependency(1), Dependency(1, "SS")))])


@pytest.mark.parametrize("tasks", [
    [task(1, predecessors="2"), task(2, predecessors="1")],
    [task(1, predecessors="3"), task(2, predecessors="1"), task(3, predecessors="2"), task(4, predecessors="3")],
])
def test_cycles_do_not_return_a_partial_or_fake_critical_schedule(tasks):
    assert_error("cycle", tasks)


@pytest.mark.parametrize("duration", [-1, 0, True, 1.5, 10001])
def test_regular_duration_is_an_explicit_positive_bounded_integer(duration):
    assert_error("invalid_duration", [task(1, duration)])


def test_milestone_requires_zero_duration_instead_of_silent_coercion():
    assert_error("invalid_duration", [task(1, 1, milestone=True)])


@pytest.mark.parametrize("task_id", [0, -1, True, "1"])
def test_invalid_task_ids_fail_before_graph_construction(task_id):
    assert_error("invalid_task_id", [task(task_id)])


@pytest.mark.parametrize("dependency", [Dependency(True), Dependency(0), Dependency(1, "XX"),
                                        Dependency(1, "SS", True), Dependency(1, "FS", 1.5), "1FS"])
def test_structured_dependencies_receive_the_same_validation_as_text(dependency):
    assert_error("invalid_dependency", [replace(task(2), dependencies=(dependency,))])


def test_constraint_upper_bound_propagates_backwards_without_inventing_negative_float():
    result = rows(plan_schedule([
        task(1), task(2, predecessors="1", constraint="snlt", constraint_day=3), task(3, 10),
    ], project_start=START))
    assert result[1].latest_start == day(2)
    assert (result[1].total_float_days, result[1].free_float_days) == (2, 0)
    assert (result[2].total_float_days, result[2].free_float_days) == (2, 2)


def test_incompatible_not_before_floor_and_fixed_constraint_fail():
    assert_error("infeasible_constraint", [task(1, start=5, constraint="mso", constraint_day=4)])
    assert_error("infeasible_constraint", [task(1, constraint="mso", constraint_day=-1)])


@pytest.mark.parametrize("spec", [
    replace(task(1), planned_start=datetime(2026, 1, 1)),
    replace(task(1), planned_start="2026-01-01"),
])
def test_dates_are_plain_dates_not_datetime_or_implicit_string_parsing(spec):
    assert_error("invalid_date", [spec])


def test_date_overflow_is_a_typed_error_not_a_partial_result():
    assert_error("date_out_of_range", [task(1, 2)], project_start=date.max)
    assert_error("date_out_of_range", [task(1), task(2, predecessors="1FS+99999999d")])


def test_deterministic_immutable_results_and_inputs():
    inputs = (task(10, 2), task(5, 3), task(7, 1, "10;5"))
    result = plan_schedule(inputs, project_start=START)
    assert result == plan_schedule(tuple(reversed(inputs)), project_start=START)
    assert result.topological_order == (5, 10, 7)
    assert inputs[2].planned_start is None
    with pytest.raises(FrozenInstanceError):
        result.tasks[0].total_float_days = 99
    with pytest.raises(FrozenInstanceError):
        inputs[0].duration_days = 99


def test_empty_graph_is_explicit_and_long_chain_does_not_use_recursion():
    empty = plan_schedule([], project_start=START)
    assert empty.tasks == empty.critical_ids == empty.critical_edges == empty.topological_order == ()
    assert empty.project_finish is None
    tasks = [task(i, predecessors=str(i - 1) if i > 1 else "") for i in range(1, 1201)]
    result = plan_schedule(tasks, project_start=START)
    assert len(result.tasks) == 1200
    assert result.project_finish == day(1199)


@pytest.mark.parametrize("first_kind, second_kind", product(["FS", "SS", "FF", "SF"], repeat=2))
def test_dates_and_float_match_exhaustive_small_graph_oracle(first_kind, second_kind):
    """Enumerate feasible start dates independently of the production graph passes."""
    tasks = [task(1, 2), task(2, 1, f"1{first_kind}+1d"), task(3, 2, f"2{second_kind}-1d")]

    def satisfies(starts):
        finishes = [starts[0] + 1, starts[1], starts[2] + 1]
        for predecessor, successor, kind, lag in [(0, 1, first_kind, 1), (1, 2, second_kind, -1)]:
            if kind == "FS" and starts[successor] < finishes[predecessor] + 1 + lag:
                return False
            if kind == "SS" and starts[successor] < starts[predecessor] + lag:
                return False
            if kind == "FF" and finishes[successor] < finishes[predecessor] + lag:
                return False
            if kind == "SF" and finishes[successor] < starts[predecessor] + lag:
                return False
        return True

    feasible = [starts for starts in product(range(9), repeat=3) if satisfies(starts)]
    horizon = min(max(starts[0] + 1, starts[1], starts[2] + 1) for starts in feasible)
    optimal = [starts for starts in feasible if max(starts[0] + 1, starts[1], starts[2] + 1) == horizon]
    result = plan_schedule(tasks, project_start=START)
    assert result.project_finish == day(horizon)
    for index, row in enumerate(result.tasks):
        earliest = min(starts[index] for starts in optimal)
        latest = max(starts[index] for starts in optimal)
        assert row.earliest_start == day(earliest)
        assert row.latest_start == day(latest)
        assert row.total_float_days == latest - earliest
        baseline = tuple((item.earliest_start - START).days for item in result.tasks)
        unchanged_others = [starts for starts in optimal if all(
            starts[other] == baseline[other] for other in range(3) if other != index
        )]
        assert row.free_float_days == max(starts[index] for starts in unchanged_others) - earliest


@pytest.mark.parametrize("kind", ["FS", "SS", "FF", "SF"])
@pytest.mark.parametrize("lag", [-2, 0, 2])
@pytest.mark.parametrize("constraint", ["mso", "mfo", "snet", "fnet", "snlt", "fnlt"])
def test_constrained_link_dates_and_float_match_independent_exhaustive_oracle(kind, lag, constraint):
    tasks = [task(1, 2), task(2, 3, f"1{kind}{lag:+}d", constraint=constraint, constraint_day=3), task(3, 7)]

    def feasible_pair(starts):
        pred_start, succ_start = starts
        pred_finish, succ_finish = pred_start + 1, succ_start + 2
        if kind == "FS" and succ_start < pred_finish + 1 + lag:
            return False
        if kind == "SS" and succ_start < pred_start + lag:
            return False
        if kind == "FF" and succ_finish < pred_finish + lag:
            return False
        if kind == "SF" and succ_finish < pred_start + lag:
            return False
        return {"mso": succ_start == 3, "mfo": succ_finish == 3,
                "snet": succ_start >= 3, "fnet": succ_finish >= 3,
                "snlt": succ_start <= 3, "fnlt": succ_finish <= 3}[constraint]

    feasible = [starts for starts in product(range(9), repeat=2) if feasible_pair(starts)]
    if not feasible:
        assert_error("infeasible_constraint", tasks)
        return
    horizon = min(max(starts[0] + 1, starts[1] + 2, 6) for starts in feasible)
    optimal = [starts for starts in feasible if max(starts[0] + 1, starts[1] + 2, 6) == horizon]
    plan = plan_schedule(tasks, project_start=START)
    indexed = rows(plan)
    assert plan.project_finish == day(horizon)
    baseline = tuple((indexed[task_id].earliest_start - START).days for task_id in [1, 2])
    for index, task_id in enumerate([1, 2]):
        earliest = min(starts[index] for starts in optimal)
        latest = max(starts[index] for starts in optimal)
        assert indexed[task_id].earliest_start == day(earliest)
        assert indexed[task_id].latest_start == day(latest)
        assert indexed[task_id].total_float_days == latest - earliest
        unchanged_other = [starts for starts in optimal if starts[1 - index] == baseline[1 - index]]
        assert indexed[task_id].free_float_days == max(starts[index] for starts in unchanged_other) - earliest
