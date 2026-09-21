"""Opt-in PostgreSQL gates for contract-derived budget proposals."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import organizations_contracts as api
from app.database import Base
from app.models.execution_finance import BudgetLine, CostCategory
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from test_mvp4_finance_source_pins_postgres import _test_url


def test_dirty_postgres_migration_adds_empty_proposal_table_without_touching_budget(monkeypatch):
    url = _test_url()
    schema = "contract_budget_migration_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "d15a7c9e2b40")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO organizations(id,name) VALUES (9401,'Contract budget org')"))
            connection.execute(text("INSERT INTO projects(id,name,organization_id) VALUES (9401,'Project',9401)"))
            connection.execute(text(
                "INSERT INTO contracts(id,project_id,number,title,contract_kind,amount,status) "
                "VALUES (9401,9401,'C-LEGACY','Legacy','supply',500,'active')"
            ))
            connection.execute(text(
                "INSERT INTO budget_lines(id,project_id,contract_id,category,description,planned_amount,committed_amount,actual_amount,forecast_amount,currency,status) "
                "VALUES (9401,9401,9401,'Legacy','Legacy line',500,75,25,500,'RUB','approved')"
            ))
        command.upgrade(config, "head")
        assert "contract_budget_proposals" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT planned_amount,committed_amount,actual_amount FROM budget_lines WHERE id=9401"
            )).one()
            assert row == (Decimal("500.00"), Decimal("75.00"), Decimal("25.00"))
            assert connection.scalar(text("SELECT count(*) FROM contract_budget_proposals")) == 0
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_parallel_confirmation_creates_one_budget_line():
    url = make_url(_test_url())
    base = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "contract_budget_race_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            organization = Organization(name="Race org")
            manager = User(name="Manager", email=f"manager-{uuid4().hex}@example.test", is_admin=False)
            db.add_all([organization, manager]); db.flush()
            project = Project(name="Race project", organization_id=organization.id)
            db.add(project); db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=manager.id, role="manager"))
            contract = Contract(project_id=project.id, number="RACE", title="Race", contract_kind="supply", amount=Decimal("900"), status="active")
            category = CostCategory(organization_id=organization.id, name="Works", normalized_name="works", is_active=True)
            db.add_all([contract, category]); db.commit()
            proposal = api.create_contract_budget_proposal(project.id, contract.id, db, manager)
            api.update_contract_budget_proposal(
                proposal["id"], api.ContractBudgetProposalUpdate(selected_cost_category_id=category.id), db, manager,
            )
            proposal_id, manager_id = proposal["id"], manager.id

        barrier = Barrier(2)

        def confirm():
            with Session(engine) as db:
                actor = db.get(User, manager_id)
                barrier.wait(timeout=10)
                return api.confirm_contract_budget_proposal(proposal_id, db, actor)["created_budget_line_id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=20) for future in (pool.submit(confirm), pool.submit(confirm))]
        assert results[0] == results[1]
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(BudgetLine)) == 1
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()
