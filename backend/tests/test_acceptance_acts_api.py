from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
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
from app.api import acceptance_acts as api


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Org")
        user = User(name="Manager", email="act-m@example.test", is_admin=False)
        viewer = User(name="Viewer", email="act-v@example.test", is_admin=False)
        db.add_all([org, user, viewer])
        db.flush()
        project = Project(name="Project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
        contract = Contract(project_id=project.id, number="1", title="Contract")
        db.add(contract)
        db.flush()
        stage = ContractStage(
            project_id=project.id,
            contract_id=contract.id,
            stage_number=1,
            title="Монтаж",
            object_name=None,
            planned_start=date(2026, 1, 1),
            planned_finish=date(2026, 2, 1),
            amount=Decimal("100000.00"),
            status="confirmed",
        )
        db.add(stage)
        db.commit()
        yield {
            "db": db,
            "project": project,
            "contract": contract,
            "stage": stage,
            "user": user,
            "viewer": viewer,
        }
    engine.dispose()


def test_create_act_returns_number(world):
    db = world["db"]
    result = api.create_act_endpoint(
        project_id=world["project"].id,
        contract_id=world["contract"].id,
        stage_id=world["stage"].id,
        db=db,
        user=world["user"],
    )
    assert result["number"] == "1"
    assert result["status"] == "draft"
    act = db.query(AcceptanceAct).filter(AcceptanceAct.id == result["id"]).one()
    assert act.stage_id == world["stage"].id


def test_docx_returns_zip_bytes(world):
    db = world["db"]
    created = api.create_act_endpoint(
        project_id=world["project"].id,
        contract_id=world["contract"].id,
        stage_id=world["stage"].id,
        db=db,
        user=world["user"],
    )
    response = api.download_act_docx(
        project_id=world["project"].id,
        act_id=created["id"],
        db=db,
        user=world["viewer"],
    )
    body = response.body
    assert isinstance(body, (bytes, bytearray))
    assert bytes(body)[:2] == b"PK"
    cd = response.headers.get("content-disposition", "")
    assert "attachment" in cd and "filename*=UTF-8''" in cd


def test_signed_creates_cash_flow_entry(world):
    db = world["db"]
    created = api.create_act_endpoint(
        project_id=world["project"].id,
        contract_id=world["contract"].id,
        stage_id=world["stage"].id,
        db=db,
        user=world["user"],
    )
    result = api.sign_act_endpoint(
        project_id=world["project"].id,
        act_id=created["id"],
        payload=api.SignedPayload(act_date=date(2026, 3, 1)),
        db=db,
        user=world["user"],
    )
    assert result["status"] == "signed"
    assert result["cash_flow_entry_id"] is not None
    entries = db.query(CashFlowEntry).filter(
        CashFlowEntry.source_name == f"acceptance_act:{created['id']}"
    ).all()
    assert len(entries) == 1
    assert entries[0].direction == "inflow"
    assert Decimal(entries[0].planned_amount) == Decimal("100000.00")


def test_viewer_cannot_create_act(world):
    db = world["db"]
    with pytest.raises(HTTPException) as exc:
        api.create_act_endpoint(
            project_id=world["project"].id,
            contract_id=world["contract"].id,
            stage_id=world["stage"].id,
            db=db,
            user=world["viewer"],
        )
    assert exc.value.status_code == 403
