"""Required CI gates, isolated schemas only; never connect to production."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app import dds_article_budget as service
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory, DdsArticleBudgetOperation
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.user import User
from test_mvp4_finance_source_pins_postgres import _test_url


@pytest.fixture
def pg_world(monkeypatch):
    base = create_engine(_test_url(), hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "dds_article_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = make_url(_test_url()).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        # Real migrations install the cash_flow_revision trigger. Metadata-only
        # tables would falsely pass the revision rollback test without it.
        command.upgrade(config, "head")
        with Session(engine) as db:
            user = User(name="Manager", email=f"{uuid4().hex}@example.test", is_admin=True)
            org = Organization(name="Article budget PG")
            db.add_all([user, org]); db.flush()
            project = Project(name="Article budget", organization_id=org.id)
            db.add(project); db.flush()
            contract = Contract(project_id=project.id, title="PG", number="PG")
            category = CostCategory(organization_id=org.id, name="Материалы", normalized_name="материалы")
            document = Document(project_id=project.id, name="matrix.xlsx", current_version=1, source="local_upload")
            db.add_all([contract, category, document]); db.flush()
            version = DocumentVersion(document_id=document.id, version_number=1,
                                      content="Статья\tГод\tянварь\tфевраль\tмарт\nМатериалы\t6\t1\t2\t3\n")
            db.add(version); db.commit()
            payload = service.ArticleBudgetPreviewRequest(project_id=project.id, contract_id=contract.id,
                          plan_year=2026, budget_revision=1, mode="import_forecast", category_by_article={2: category.id})
            proposal = service.preview_article_budget(document.id, payload, db, user)
            apply_payload = service.ArticleBudgetApplyRequest(**payload.model_dump(), preview_hash=proposal["preview_hash"],
                          expected_document_version_id=version.id, expected_document_sha256=proposal["document_sha256"],
                          idempotency_key="pg-article-operation", owner_confirmed=True)
            yield engine, document.id, user.id, apply_payload
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_parallel_same_key_creates_one_atomic_operation(pg_world):
    engine, document_id, user_id, payload = pg_world
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            return service.apply_article_budget(document_id, payload, db, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    assert results[0]["operation_id"] == results[1]["operation_id"]
    assert sorted(result["replayed"] for result in results) == [False, True]
    with Session(engine) as db:
        assert db.query(BudgetLine).count() == 1
        assert db.query(CashFlowEntry).count() == 3
        assert db.query(DdsArticleBudgetOperation).count() == 1


def test_failure_after_flush_rolls_back_money_receipt_and_project_revision(pg_world, monkeypatch):
    engine, document_id, user_id, payload = pg_world
    with Session(engine) as db:
        user = db.get(User, user_id)
        project = db.get(Project, payload.project_id)
        revision_before = project.cash_flow_revision
        def fault(**_values):
            raise RuntimeError("after_new_rows_fault")
        monkeypatch.setattr(service, "AuditLog", fault)
        with pytest.raises(RuntimeError, match="after_new_rows_fault"):
            service.apply_article_budget(document_id, payload, db, user)
        db.expire_all()
        assert db.query(BudgetLine).count() == 0
        assert db.query(CashFlowEntry).count() == 0
        assert db.query(DdsArticleBudgetOperation).count() == 0
        assert db.get(Project, payload.project_id).cash_flow_revision == revision_before


def test_additive_migration_keeps_legacy_finances_and_refuses_history_loss(monkeypatch):
    base = create_engine(_test_url(), hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "dds_article_migration_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = make_url(_test_url()).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped, hide_parameters=True)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "c70a0090f001")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES(9801,'DDS org')"))
            connection.execute(text("INSERT INTO projects(id,name,organization_id) VALUES(9801,'DDS project',9801)"))
            connection.execute(text("INSERT INTO cash_flow_entries(id,project_id,direction,title,planned_date,planned_amount,actual_amount,currency,status) VALUES(9801,9801,'outflow','Legacy','2026-01-31',14811906.53,0,'RUB','proposed')"))
            before = connection.execute(text("SELECT planned_amount,actual_amount,planned_date,status,budget_line_id,schedule_item_id,record_version FROM cash_flow_entries WHERE id=9801")).one()
        command.upgrade(config, "head")
        with engine.begin() as connection:
            after = connection.execute(text("SELECT planned_amount,actual_amount,planned_date,status,budget_line_id,schedule_item_id,record_version FROM cash_flow_entries WHERE id=9801")).one()
            assert before == after
            assert connection.scalar(text("SELECT entry_kind FROM cash_flow_entries WHERE id=9801")) == "legacy_unclassified"
            connection.execute(text("UPDATE cash_flow_entries SET entry_kind='plan_forecast' WHERE id=9801"))
        with pytest.raises(RuntimeError, match="DDS_ARTICLE_DOWNGRADE_BLOCKED"):
            command.downgrade(config, "c70a0090f001")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT entry_kind FROM cash_flow_entries WHERE id=9801")) == "plan_forecast"
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()
