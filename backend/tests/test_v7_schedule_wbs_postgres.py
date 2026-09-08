"""Owned PostgreSQL acceptance for the a20 -> a21 WBS hierarchy.

Every test operates in the disposable UUID schema supplied by ``pg_graph``.
SQLite is deliberately not accepted as evidence for locking or migrations.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from alembic import command
from fastapi import HTTPException
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import app.api.execution_finance as api
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.user import User
from test_v7_schedule_graph_postgres import pg_graph  # noqa: F401
from test_v7_schedule_graph_rows import body_for
from test_v7_schedule_wbs import leaf, summary


def _wbs_body(rows, *, expected_revision=1, suffix=""):
    body = body_for([], expected_graph_revision=expected_revision,
                    deleted_ids=[row.id for row in rows])
    body["items"] = [
        summary("phase", "Synthetic phase" + suffix, order=0),
        summary("work", "Synthetic work" + suffix, "phase", 0),
        leaf("first", "Synthetic first" + suffix, "work", 2, 0),
        leaf("second", "Synthetic second" + suffix, "work", 3, 1),
    ]
    body["items"][3]["dependencies"] = [
        {"predecessor_ref": "first", "link_type": "FS", "lag_days": 0}
    ]
    return body


def _put(db, actor_id, baseline_id, body):
    return api.put_schedule_graph_rows(
        baseline_id, api.ScheduleGraphRowsPut.model_validate(body), db,
        db.get(User, actor_id),
    )


def test_pg_wbs_clean_head_and_existing_flat_rows_upgrade(pg_graph):
    engine, config, (_, _, row_ids) = pg_graph
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a21"
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE wbs_parent_id IS NOT NULL")) == 0
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE wbs_order <> 0 OR is_summary")) == 0
        before = list(db.execute(text(
            "SELECT id, project_id, baseline_id, title FROM schedule_items ORDER BY id"
        )))

    # The real migration must preserve rows created under a20, not merely build
    # a clean a21 schema. Defaults contain no hierarchy intent, so downgrade is safe.
    command.downgrade(config, "a54f001c0a20")
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a20"
        assert db.scalar(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='schedule_items' "
            "AND column_name IN ('wbs_parent_id','wbs_order','is_summary')"
        )) == 0
    command.upgrade(config, "a54f001c0a21")
    with engine.connect() as db:
        after = list(db.execute(text(
            "SELECT id, project_id, baseline_id, title FROM schedule_items ORDER BY id"
        )))
        assert after == before
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE wbs_parent_id IS NOT NULL")) == 0
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE wbs_order <> 0 OR is_summary")) == 0


def _assert_constraint_rejects(pg_graph, statement):
    engine, _, (_, _, row_ids) = pg_graph
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(text(statement), {"id": row_ids[0]})
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE wbs_order < 0 OR is_summary")) == 0


def test_pg_wbs_order_constraint_rejects_negative_value(pg_graph):
    _assert_constraint_rejects(
        pg_graph, "UPDATE schedule_items SET wbs_order=-1 WHERE id=:id")


def test_pg_wbs_summary_constraint_rejects_leaf_intent(pg_graph):
    _assert_constraint_rejects(
        pg_graph, "UPDATE schedule_items SET is_summary=true WHERE id=:id")


def test_pg_wbs_service_rejects_cross_baseline_parent(pg_graph):
    engine, _, (actor_id, baseline_id, row_ids) = pg_graph
    with Session(engine) as db:
        original = db.get(ScheduleBaseline, baseline_id)
        other = ScheduleBaseline(project_id=original.project_id, contract_id=original.contract_id,
                                 created_by_user_id=actor_id, name="Synthetic other", version=2)
        db.add(other); db.flush()
        foreign_parent = ScheduleItem(project_id=original.project_id, baseline_id=other.id,
                                      title="Synthetic foreign summary", is_summary=True)
        db.add(foreign_parent); db.flush()
        db.execute(text("UPDATE schedule_items SET wbs_parent_id=:parent WHERE id=:child"),
                   {"parent": foreign_parent.id, "child": row_ids[0]})
        db.commit()
        with pytest.raises(HTTPException) as caught:
            api.get_schedule_graph(baseline_id, db, db.get(User, actor_id))
        assert (caught.value.status_code, caught.value.detail) == (409, "schedule_wbs_scope_changed")


def test_pg_wbs_concurrent_complete_graph_has_one_winner_and_persists_rollup(pg_graph):
    engine, _, (actor_id, baseline_id, row_ids) = pg_graph
    barrier = Barrier(2, timeout=10)

    def invoke(suffix):
        with Session(engine) as db:
            rows = [db.get(ScheduleItem, identifier) for identifier in row_ids]
            body = _wbs_body(rows, suffix=suffix)
            barrier.wait()
            try:
                return 200, _put(db, actor_id, baseline_id, body)
            except HTTPException as error:
                db.rollback()
                return error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=25) for future in (
            pool.submit(invoke, " A"), pool.submit(invoke, " B")
        )]
    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(result for status, result in results if status == 200)
    assert winner["graph_revision"] == 2
    assert winner["items"][0]["planned_start"] == date(2026, 9, 1)
    assert winner["items"][0]["planned_finish"] == date(2026, 9, 5)

    with Session(engine) as db:
        stored = api.get_schedule_graph(baseline_id, db, db.get(User, actor_id))
        assert stored["graph_revision"] == 2
        summaries = [item for item in stored["items"] if item["is_summary"]]
        assert len(summaries) == 2
        assert all((item["planned_start"], item["planned_finish"]) ==
                   (date(2026, 9, 1), date(2026, 9, 5)) for item in summaries)
        assert db.scalar(select(ScheduleBaseline.graph_revision).where(
            ScheduleBaseline.id == baseline_id)) == 2


def test_pg_wbs_clone_remaps_parent_and_dependency_ids(pg_graph):
    engine, _, (actor_id, baseline_id, row_ids) = pg_graph
    with Session(engine) as db:
        rows = [db.get(ScheduleItem, identifier) for identifier in row_ids]
        saved = _put(db, actor_id, baseline_id, _wbs_body(rows))
        source_ids = {item["id"] for item in saved["items"]}
        baseline = db.get(ScheduleBaseline, baseline_id)
        baseline.status = "approved"
        db.commit()
        cloned = api.clone_baseline(baseline_id, api.BaselineClone(expected_version=1),
                                    db, db.get(User, actor_id))
        clone_rows = list(db.scalars(select(ScheduleItem).where(
            ScheduleItem.baseline_id == cloned["id"]).order_by(ScheduleItem.id)))
        clone_ids = {row.id for row in clone_rows}
        assert source_ids.isdisjoint(clone_ids)
        assert all(row.wbs_parent_id is None or row.wbs_parent_id in clone_ids for row in clone_rows)
        assert any(row.wbs_parent_id is not None for row in clone_rows)
        dependency_ids = {
            link.predecessor_id
            for row in clone_rows
            for link in api.parse_dependencies(row.predecessor_ids)
        }
        assert dependency_ids and dependency_ids <= clone_ids


def test_pg_wbs_downgrade_refuses_hierarchy_intent(pg_graph):
    engine, config, (actor_id, baseline_id, row_ids) = pg_graph
    with Session(engine) as db:
        rows = [db.get(ScheduleItem, identifier) for identifier in row_ids]
        _put(db, actor_id, baseline_id, _wbs_body(rows))
    try:
        command.downgrade(config, "a54f001c0a20")
    except DBAPIError as error:
        assert getattr(error.orig, "sqlstate", None) == "P0001"
        assert error.orig.diag.message_primary == "schedule_wbs_downgrade_requires_verified_restore"
    else:
        pytest.fail("wbs_downgrade_did_not_refuse_hierarchy_intent")
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a21"
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE is_summary")) == 2
