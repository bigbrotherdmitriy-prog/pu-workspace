"""Whole-batch CAS/undo with real migrations and only synthetic financial data."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app import dds_budget_links as service
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry, DdsBudgetLinkOperation
from app.models.project import Project
from app.models.user import User
from test_dds_budget_links import build_world, payload
from test_mvp4_finance_source_pins_postgres import _test_url


@pytest.fixture
def pg_world(monkeypatch):
    url = _test_url()  # Explicit opt-in and test database name guard.
    base = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "dds_links_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=10s"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "head")
        with Session(engine) as db:
            user = User(name="Synthetic manager", email=f"{uuid4().hex}@example.test", is_admin=True)
            db.add(user); db.flush()
            world = build_world(db, user)
            request = payload(world)
            identifiers = [row.id for row in world.rows]
            context = engine, world.document.id, user.id, request, identifiers, config
        # The migration gate drops FK constraints. No fixture session may keep
        # read locks on their referenced tables while that DDL is running.
        yield context
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


@pytest.fixture
def historical_link_migration_pg(monkeypatch):
    """Pin the historical b001 -> b002 gate; current APIs require later columns."""
    url = _test_url()
    base = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "dds_links_migration_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=10s"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "d021a6c0b001")
        # SQL-only historical data: neither modern model defaults nor current
        # financial creators may introduce business data from later revisions.
        with engine.begin() as connection:
            statements = (
                "INSERT INTO organizations(id,name) VALUES(9701,'Synthetic historical link tenant')",
                "INSERT INTO users(id,name,email,is_admin) VALUES(9701,'Synthetic owner','historical-links@example.test',true)",
                "INSERT INTO projects(id,name,organization_id) VALUES(9701,'Synthetic historical links',9701)",
                "INSERT INTO contracts(id,project_id,number,title,contract_kind,amount,advance_amount,retention_percent,"
                "signed_at,warranty_until,status,record_version) VALUES "
                "(9701,9701,'S-HISTORICAL','Synthetic historical contract','customer',1200.25,200.00,22.00,"
                "'2026-01-01','2027-01-01','draft',6)",
                "INSERT INTO documents(id,project_id,name,source,status,current_version) "
                "VALUES(9701,9701,'synthetic-historical-matrix.xlsx','local_upload','discovered',1)",
                "INSERT INTO document_versions(id,document_id,version_number,content) "
                "VALUES(9701,9701,1,'Synthetic historical matrix')",
                "INSERT INTO budget_lines(id,project_id,contract_id,category,description,planned_amount,committed_amount,"
                "actual_amount,forecast_amount,currency,status,line_kind,budget_period,budget_revision,"
                "article_normalized_name,record_version) VALUES "
                "(9701,9701,9701,'Synthetic','Historical article budget',999.98,200.00,0,999.98,'RUB',"
                "'proposed','article_budget',2026,1,'synthetic historical article',4)",
                "INSERT INTO cash_flow_entries(id,project_id,contract_id,direction,title,planned_date,actual_date,"
                "planned_amount,actual_amount,currency,status,record_version,entry_kind) VALUES "
                "(9701,9701,9701,'outflow','Synthetic historical forecast','2026-01-31',NULL,999.98,0,'RUB',"
                "'proposed',3,'legacy_unclassified')",
                "INSERT INTO contract_versions(contract_id,project_id,sequence,event,resulting_record_version,"
                "snapshot,changed_fields,actor_user_id) VALUES(9701,9701,1,'created',6,"
                "CAST(:contract_snapshot AS json),CAST(:contract_changed_fields AS json),9701)",
                "INSERT INTO dds_article_budget_operations(id,project_id,contract_id,actor_user_id,source_document_id,"
                "source_document_version_id,source_document_sha256,mode,budget_period,budget_revision,idempotency_key,"
                "request_hash,preview_hash,algorithm_version,result_json,snapshot_json) VALUES "
                "(9701,9701,9701,9701,9701,9701,repeat('a',64),'create_budget',2026,1,'synthetic-historical-article',"
                "repeat('b',64),repeat('c',64),'historical-synthetic',:article_result_json,:article_snapshot_json)",
                "INSERT INTO audit_logs(action,entity_type,entity_id,details) VALUES "
                "('dds_article_budget_applied','document',9701,'Synthetic historical article receipt')",
            )
            json_parameters = {
                "contract_snapshot": '{"amount":"1200.25","retention_percent":"22.00"}',
                "contract_changed_fields": '["amount"]',
                "article_result_json": '{"created_budget_ids":[9701]}',
                "article_snapshot_json": '{"budget_lines":[{"id":9701,"planned_amount":"999.98"}]}',
            }
            for statement in statements:
                connection.execute(text(statement), json_parameters)
        yield engine, config
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_concurrent_apply_has_one_receipt_and_one_audit_per_link(pg_world):
    engine, document_id, user_id, request, ids, _ = pg_world
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            return service.apply_budget_links(document_id, request, db, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    assert results[0]["operation_id"] == results[1]["operation_id"]
    assert sorted(item["replayed"] for item in results) == [False, True]
    with Session(engine) as db:
        assert db.query(DdsBudgetLinkOperation).count() == 1
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_applied").count() == len(ids)
        assert all(db.get(CashFlowEntry, i).record_version == 2 for i in ids)


def test_other_session_update_rejects_stale_preview_without_partial_writes(pg_world):
    engine, document_id, user_id, request, ids, _ = pg_world
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, ids[1])
        row.planned_amount += Decimal("0.01"); row.record_version += 1; writer.commit()
    with Session(engine) as db:
        with pytest.raises(HTTPException, match="CASH_FLOW_VERSION_MISMATCH"):
            service.apply_budget_links(document_id, request, db, db.get(User, user_id))
        assert all(db.get(CashFlowEntry, i).budget_line_id is None for i in ids)
        assert db.query(DdsBudgetLinkOperation).count() == 0


def test_audit_failure_rolls_back_links_receipt_and_trigger_revision(pg_world, monkeypatch):
    engine, document_id, user_id, request, ids, _ = pg_world
    with Session(engine) as db:
        before_revision = db.get(Project, request.project_id).cash_flow_revision
        original = service.AuditLog
        count = 0
        def fault(**values):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic-second-audit-fault")
            return original(**values)
        monkeypatch.setattr(service, "AuditLog", fault)
        with pytest.raises(RuntimeError, match="synthetic-second-audit-fault"):
            service.apply_budget_links(document_id, request, db, db.get(User, user_id))
        db.expire_all()
        assert db.get(Project, request.project_id).cash_flow_revision == before_revision
        assert db.query(DdsBudgetLinkOperation).count() == 0
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_applied").count() == 0
        assert all(db.get(CashFlowEntry, i).budget_line_id is None and db.get(CashFlowEntry, i).record_version == 1 for i in ids)


def test_whole_undo_cas_and_parallel_retry(pg_world):
    engine, document_id, user_id, request, ids, _ = pg_world
    with Session(engine) as db:
        result = service.apply_budget_links(document_id, request, db, db.get(User, user_id))
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, ids[1]); row.note = "Synthetic edit"; row.record_version += 1; writer.commit()
    with Session(engine) as db:
        with pytest.raises(HTTPException, match="LINK_UNDO_STALE"):
            service.undo_budget_links(result["operation_id"], db, db.get(User, user_id))
        assert all(db.get(CashFlowEntry, i).budget_line_id is not None for i in ids)
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_undone").count() == 0
    # Restore the exact saved state in this isolated fixture only, then test a
    # real concurrent undo retry (not a second per-row unlink operation).
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, ids[1]); row.note = None; row.record_version = 2; writer.commit()
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id); barrier.wait(timeout=20)
            return service.undo_budget_links(result["operation_id"], db, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=40) for future in [executor.submit(run) for _ in range(2)]]
    assert all(item["undone"] for item in results)
    assert sorted(item["replayed"] for item in results) == [False, True]
    with Session(engine) as db:
        assert all(db.get(CashFlowEntry, i).budget_line_id is None and db.get(CashFlowEntry, i).record_version == 3 for i in ids)
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_undone").count() == len(ids)


def test_additive_migration_and_history_preserving_downgrade(historical_link_migration_pg):
    engine, config = historical_link_migration_pg
    queries = {
        "cash_flow": "SELECT id,planned_amount,actual_amount,planned_date,actual_date,status,budget_line_id,"
                     "schedule_item_id,record_version,entry_kind FROM cash_flow_entries ORDER BY id",
        "budget": "SELECT id,planned_amount,committed_amount,actual_amount,forecast_amount,currency,status,"
                  "line_kind,budget_period,budget_revision,record_version FROM budget_lines ORDER BY id",
        "article_history": "SELECT * FROM dds_article_budget_operations ORDER BY id",
        "contract_history": "SELECT id,snapshot::text,changed_fields::text,resulting_record_version FROM contract_versions ORDER BY id",
        "contracts": "SELECT * FROM contracts ORDER BY id",
        "audit": "SELECT * FROM audit_logs ORDER BY id",
    }

    def preserved_data():
        with engine.connect() as connection:
            return {name: connection.execute(text(query)).all() for name, query in queries.items()}

    before = preserved_data()
    assert before["cash_flow"][0].planned_amount == Decimal("999.98")
    assert before["budget"][0].planned_amount == Decimal("999.98")
    assert before["article_history"] and before["contract_history"] and before["audit"]
    assert "vat_snapshot" not in {column["name"] for column in inspect(engine).get_columns("budget_lines")}
    assert "vat_mode" not in {column["name"] for column in inspect(engine).get_columns("contracts")}
    command.upgrade(config, "d021a6c0b002")
    assert preserved_data() == before
    # Only the historical link migration is traversed. Later commercial
    # downgrade guards remain intact and are tested in their own isolated gate.
    command.downgrade(config, "d021a6c0b001")
    assert preserved_data() == before
    command.upgrade(config, "d021a6c0b002")
    assert preserved_data() == before
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO dds_budget_link_operations(project_id,contract_id,actor_user_id,source_document_id,"
            "source_document_version_id,source_document_sha256,currency,budget_period,budget_revision,"
            "idempotency_key,request_hash,preview_hash,result_json,snapshot_json,undone_at) VALUES "
            "(9701,9701,9701,9701,9701,repeat('a',64),'RUB',2026,1,'synthetic-historical-undone-link',"
            "repeat('d',64),repeat('e',64),:result_json,:snapshot_json,'2026-02-01T00:00:00Z')"
        ), {
            "result_json": '{"undone":true,"linked_cash_flow_ids":[9701]}',
            "snapshot_json": '{"rows":[{"id":9701,"budget_line_id":null,"record_version":3}]}',
        })
        link_history = connection.execute(text("SELECT * FROM dds_budget_link_operations ORDER BY id")).all()
    with pytest.raises(RuntimeError, match="DDS_BUDGET_LINK_DOWNGRADE_BLOCKED"):
        command.downgrade(config, "d021a6c0b001")
    assert preserved_data() == before
    with engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM dds_budget_link_operations ORDER BY id")).all() == link_history
        assert len(link_history) == 1 and link_history[0].undone_at is not None
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "d021a6c0b002"
