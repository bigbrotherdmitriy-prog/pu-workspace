from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app import acceptance_acts as aa


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Наша")
        counterparty = Organization(name="Контрагент")
        user = User(name="Manager", email="act-m@example.test", is_admin=False)
        db.add_all([org, counterparty, user])
        db.flush()
        project = Project(name="Project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        contract = Contract(project_id=project.id, number="1", title="Contract")
        db.add(contract)
        db.commit()
        yield {"db": db, "project": project, "contract": contract, "user": user}
    engine.dispose()


def test_next_act_number_empty(world):
    db = world["db"]
    contract = world["contract"]
    assert aa.next_act_number(db, contract.id) == "1"


def test_next_act_number_increments(world):
    db = world["db"]
    contract = world["contract"]
    project = world["project"]
    db.add(AcceptanceAct(
        project_id=project.id, contract_id=contract.id,
        number="1", title="t", amount=Decimal("100.00"),
    ))
    db.add(AcceptanceAct(
        project_id=project.id, contract_id=contract.id,
        number="7", title="t", amount=Decimal("100.00"),
    ))
    db.commit()
    assert aa.next_act_number(db, contract.id) == "8"


def test_payment_days_working():
    text = "Оплата производится в течение 10 (десяти) рабочих дней с даты подписания"
    assert aa.payment_days(text) == (10, True)


def test_payment_days_default():
    assert aa.payment_days("ничего про сроки") == (10, True)


def test_add_working_days_friday_to_monday():
    friday = date(2026, 10, 23)
    assert aa.add_working_days(friday, 1) == date(2026, 10, 26)


def test_create_stage_act_idempotent(world):
    db = world["db"]
    contract = world["contract"]
    project = world["project"]
    user = world["user"]
    stage = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name=None, stage_number=1, title="Работы",
        amount=Decimal("100.00"),
    )
    db.add(stage)
    db.commit()

    act1 = aa.create_stage_act(db, project.id, contract, stage, user)
    db.commit()
    act2 = aa.create_stage_act(db, project.id, contract, stage, user)
    db.commit()
    assert act1.id == act2.id
    assert db.query(AcceptanceAct).filter_by(stage_id=stage.id).count() == 1


def test_mark_act_signed_creates_one_inflow(world):
    db = world["db"]
    contract = world["contract"]
    project = world["project"]
    user = world["user"]
    stage = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name=None, stage_number=1, title="Работы",
        amount=Decimal("100.00"),
    )
    db.add(stage)
    db.commit()

    act = aa.create_stage_act(db, project.id, contract, stage, user)
    db.commit()
    aa.mark_act_signed(db, act, date(2026, 10, 23), user)
    db.commit()
    assert act.status == "signed"
    assert act.act_date == date(2026, 10, 23)
    rows = db.query(CashFlowEntry).filter(CashFlowEntry.direction == "inflow").all()
    assert len(rows) == 1
    assert act.cash_flow_entry_id == rows[0].id

    aa.mark_act_signed(db, act, date(2026, 10, 23), user)
    db.commit()
    rows = db.query(CashFlowEntry).filter(CashFlowEntry.direction == "inflow").all()
    assert len(rows) == 1


def test_build_act_data_counterparty_fallback_without_org(world):
    db = world["db"]
    project = world["project"]
    contract = world["contract"]
    contract.counterparty = "ООО Заказчик"
    contract.counterparty_organization_id = None
    contract.contract_kind = "customer"
    stage = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name=None, stage_number=1, title="Работы",
        amount=Decimal("100.00"),
    )
    db.add(stage)
    db.commit()

    data = aa.build_act_data(db, project.id, contract, stage, None)
    assert data.customer.name == "ООО Заказчик"
    assert data.customer.signer_name == ""


def test_create_stage_act_with_explicit_number(world):
    db = world["db"]
    contract = world["contract"]
    project = world["project"]
    user = world["user"]
    first = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name=None, stage_number=1, title="Работы",
        amount=Decimal("100.00"),
    )
    second = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name=None, stage_number=2, title="Работы 2",
        amount=Decimal("200.00"),
    )
    db.add_all([first, second])
    db.commit()

    act = aa.create_stage_act(db, project.id, contract, first, user, number="12")
    db.commit()
    assert act.number == "12"

    with pytest.raises(Exception) as exc:
        aa.create_stage_act(db, project.id, contract, second, user, number="12")
    assert getattr(exc.value, "status_code", None) == 409


def test_stage_register_coefficient(world):
    db = world["db"]
    contract = world["contract"]
    project = world["project"]
    first = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name="ДУБНА", stage_number=1, title="Этап 1",
        amount=Decimal("23446972.13"),
    )
    second = ContractStage(
        project_id=project.id, contract_id=contract.id,
        object_name="ДУБНА", stage_number=2, title="Этап 2",
        amount=Decimal("238245248.73"),
    )
    db.add_all([first, second])
    db.commit()

    total = aa.object_total(db, first)
    assert total == Decimal("261692220.86")
    quantity = aa.stage_quantity(first.amount, total)
    assert str(quantity) == "0.08960"
