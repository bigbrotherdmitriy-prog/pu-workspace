"""Opt-in real PostgreSQL row lifecycle races; never claims SQLite concurrency."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.execution_finance as api
from app.models.audit_log import AuditLog
from app.models.execution_finance import BudgetLine, ScheduleItem
from app.models.user import User
from test_v7_schedule_graph_postgres import pg_graph  # noqa: F401 - isolated migrated PG fixture
from test_v7_schedule_graph_rows import body_for, row_body


def test_pg_concurrent_complete_row_batches_have_one_winner(pg_graph):
    engine, _, (actor_id, baseline_id, row_ids) = pg_graph
    barrier = Barrier(2, timeout=10)
    def invoke(reference):
        with Session(engine) as db:
            actor = db.get(User, actor_id)
            rows = [db.get(ScheduleItem, identifier) for identifier in row_ids]
            body = body_for(rows); body["items"].append(row_body(client_ref=reference))
            barrier.wait()
            try:
                result = api.put_schedule_graph_rows(baseline_id, api.ScheduleGraphRowsPut(**body), db, actor)
                return 200, result["client_ref_map"]
            except HTTPException as error:
                db.rollback(); return error.status_code, {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, ref) for ref in ("writer_a", "writer_b")]
        results = [future.result(timeout=25) for future in futures]
    assert sorted(status for status, _ in results) == [200, 409]
    with Session(engine) as db:
        result = api.get_schedule_graph(baseline_id, db, db.get(User, actor_id))
        assert result["graph_revision"] == 2 and len(result["items"]) == 3
        assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "schedule_graph_rows_saved")) == 1


def test_pg_existing_uncommitted_fk_link_blocks_then_protects_delete(pg_graph):
    engine, _, (actor_id, baseline_id, row_ids) = pg_graph
    linked = Event(); delete_started = Event()
    def link_first():
        with Session(engine) as db:
            row = db.get(ScheduleItem, row_ids[0])
            db.add(BudgetLine(project_id=row.project_id, schedule_item_id=row.id,
                             category="Synthetic", description="Synthetic protected row"))
            db.flush()  # real FK key-share lock, uncommitted when delete starts
            linked.set()
            assert delete_started.wait(10)
            db.commit()
    def delete_later():
        assert linked.wait(10)
        with Session(engine) as db:
            rows = [db.get(ScheduleItem, identifier) for identifier in row_ids]
            body = body_for(rows[1:], deleted_ids=[row_ids[0]])
            delete_started.set()
            try:
                api.put_schedule_graph_rows(baseline_id, api.ScheduleGraphRowsPut(**body), db, db.get(User, actor_id))
            except HTTPException as error:
                db.rollback()
                return error.status_code, error.detail
            return 200, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(link_first); second = pool.submit(delete_later)
        first.result(timeout=25)
        assert second.result(timeout=25) == (409, "schedule_row_delete_protected")
    with Session(engine) as db:
        assert db.get(ScheduleItem, row_ids[0]) is not None
        assert db.scalar(select(BudgetLine.schedule_item_id)) == row_ids[0]
        assert api.get_schedule_graph(baseline_id, db, db.get(User, actor_id))["graph_revision"] == 1
