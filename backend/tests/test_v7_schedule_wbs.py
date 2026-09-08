"""Synthetic WBS hierarchy acceptance; no client documents or provider calls."""
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.api.execution_finance as api
from app.models.execution_finance import BudgetLine, ScheduleItem
from test_v7_schedule_graph_api import world
from test_v7_schedule_graph_rows import body_for, invoke, row_body


def summary(ref: str, title: str, parent_ref: str | None = None, order: int = 0):
    return dict(client_ref=ref, title=title, duration_days=None, is_milestone=None,
                constraint_type=None, constraint_date=None, not_before_date=None,
                dependencies=[], parent_ref=parent_ref, parent_id=None,
                wbs_order=order, is_summary=True)


def leaf(ref: str, title: str, parent_ref: str, duration: int, order: int):
    return row_body(client_ref=ref, title=title, duration_days=duration,
                    parent_ref=parent_ref, parent_id=None, wbs_order=order,
                    is_summary=False)


def test_wbs_hierarchy_order_and_rollup_are_persisted(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    payload = body_for([], deleted_ids=[row.id for row in rows])
    payload["items"] = [
        summary("phase", "Phase", order=2),
        summary("work", "Work", "phase", 1),
        leaf("late", "Late subwork", "work", 3, 2),
        leaf("early", "Early subwork", "work", 2, 1),
    ]
    payload["items"][2]["dependencies"] = [dict(predecessor_ref="early", link_type="FS", lag_days=0)]
    result = invoke(db_session, actor, baseline, payload)
    mapping = result["client_ref_map"]
    assert [row["title"] for row in result["items"]] == ["Phase", "Work", "Early subwork", "Late subwork"]
    by_title = {row["title"]: row for row in result["items"]}
    assert by_title["Phase"]["wbs_level"] == 1
    assert by_title["Work"]["wbs_parent_id"] == mapping["phase"]
    assert by_title["Early subwork"]["wbs_level"] == 3
    assert by_title["Work"]["planned_start"] == date(2026, 9, 1)
    assert by_title["Work"]["planned_finish"] == date(2026, 9, 5)
    assert by_title["Phase"]["planned_finish"] == date(2026, 9, 5)
    assert {task["task_id"] for task in result["plan"]["tasks"]} == {mapping["early"], mapping["late"]}


@pytest.mark.parametrize("fault", ["cycle", "leaf_parent", "too_deep", "missing_parent", "summary_dependency"])
def test_invalid_wbs_is_atomic(db_session, user_factory, fault):
    actor, baseline, rows = world(db_session, user_factory)
    payload = body_for([], deleted_ids=[row.id for row in rows])
    payload["items"] = [summary("p", "Phase"), summary("w", "Work", "p"), leaf("x", "Subwork", "w", 1, 0)]
    if fault == "cycle": payload["items"][0]["parent_ref"] = "w"
    if fault == "leaf_parent": payload["items"].append(leaf("y", "Child", "x", 1, 0))
    if fault == "too_deep":
        payload["items"] = [summary("a", "A1"), summary("b", "B2", "a"), summary("c", "C3", "b"),
                            summary("d", "D4", "c"), summary("e", "E5", "d"), leaf("f", "F6", "e", 1, 0)]
    if fault == "missing_parent": payload["items"][2]["parent_ref"] = "absent"
    if fault == "summary_dependency": payload["items"][0]["dependencies"] = [dict(predecessor_ref="x")]
    with pytest.raises((HTTPException, ValueError)):
        invoke(db_session, actor, baseline, payload)
    db_session.expire_all()
    assert baseline.graph_revision == 1
    assert len(list(db_session.scalars(select(ScheduleItem)))) == 2


def test_summary_cannot_receive_fact_or_financial_link(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    row = rows[0]
    row.is_summary = True
    row.duration_days = row.is_milestone = None
    db_session.commit()
    baseline.status = "approved"
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        api.update_schedule(row.id, api.ScheduleProgress(actual_progress=20, expected_actual_progress=0), db_session, actor)
    assert caught.value.detail == "schedule_summary_is_derived"
    with pytest.raises(HTTPException) as caught:
        api._validate_control_links(db_session, project_id=baseline.project_id, contract_id=None,
            schedule_item_id=row.id, task_id=None, budget_line_id=None, source_document_id=None,
            evidence_id=None, evidence_revision=None, evidence_assessment_version=None, confidence=None)
    assert caught.value.detail == "schedule_summary_link_forbidden"


def test_deleting_parent_without_children_is_required(db_session, user_factory):
    actor, baseline, rows = world(db_session, user_factory)
    rows[1].wbs_parent_id = rows[0].id
    rows[0].is_summary = True
    rows[0].duration_days = rows[0].is_milestone = None
    db_session.commit()
    payload = body_for(rows[1:], deleted_ids=[rows[0].id])
    with pytest.raises(HTTPException) as caught:
        invoke(db_session, actor, baseline, payload)
    assert caught.value.detail == "schedule_wbs_parent_delete_protected"
