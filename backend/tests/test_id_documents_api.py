from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.api import id_documents as api


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
        counterparty = Organization(
            name="Counterparty", legal_name="Counterparty LLC",
            ogrn="1027700000001", inn="7700000001", legal_address="Address",
        )
        user = User(name="Manager", email="id-m@example.test", is_admin=False)
        viewer = User(name="Viewer", email="id-v@example.test", is_admin=False)
        db.add_all([org, counterparty, user, viewer])
        db.flush()
        project = Project(name="Project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
        contract = Contract(
            project_id=project.id, number="1", title="Contract",
            counterparty="Counterparty",
            counterparty_organization_id=counterparty.id,
        )
        db.add(contract)
        db.commit()
        yield {
            "db": db,
            "project": project,
            "contract": contract,
            "user": user,
            "viewer": viewer,
        }
    engine.dispose()


def _payload(contract_id: int):
    return api.AosrPayload(
        contract_id=contract_id,
        object_name="Дом 1",
        number="1",
        act_date=date(2026, 3, 1),
        works="Монтаж",
        design_docs="Чертежи",
        materials=[api.MaterialPayload(name="Бетон", quality_doc="Паспорт")],
        supporting_docs=["АОСР"],
        work_start=date(2026, 1, 1),
        work_finish=date(2026, 2, 1),
        norms=["СП 1"],
        next_works="Отделка",
        object_address="Улица",
    )


def test_check_reports_missing_builder_order(world):
    db = world["db"]
    api.create_participant(
        project_id=world["project"].id,
        body=api.ParticipantCreate(
            role="customer_control",
            full_name="Иванов И.И.",
            order_ref="Приказ №1",
            nrs_id="НРС-1",
        ),
        db=db,
        user=world["user"],
    )
    api.create_participant(
        project_id=world["project"].id,
        body=api.ParticipantCreate(
            role="builder",
            full_name="Петров П.П.",
        ),
        db=db,
        user=world["user"],
    )
    result = api.check_aosr(
        project_id=world["project"].id,
        body=_payload(world["contract"].id),
        db=db,
        user=world["user"],
    )
    missing = result["missing"]
    assert any(
        "Представитель лица, осуществляющего строительство" in m
        and "приказ" in m
        for m in missing
    )


def test_docx_returns_zip_bytes(world):
    db = world["db"]
    api.create_participant(
        project_id=world["project"].id,
        body=api.ParticipantCreate(
            role="customer_control",
            full_name="Иванов И.И.",
            order_ref="Приказ №1",
            nrs_id="НРС-1",
        ),
        db=db,
        user=world["user"],
    )
    response = api.download_aosr_docx(
        project_id=world["project"].id,
        body=_payload(world["contract"].id),
        db=db,
        user=world["user"],
    )
    body = response.body
    assert isinstance(body, (bytes, bytearray))
    assert bytes(body)[:2] == b"PK"
    cd = response.headers.get("content-disposition", "")
    assert "attachment" in cd and "AOSR-" in cd


def test_viewer_cannot_create_participant(world):
    db = world["db"]
    with pytest.raises(HTTPException) as exc:
        api.create_participant(
            project_id=world["project"].id,
            body=api.ParticipantCreate(
                role="builder",
                full_name="Петров П.П.",
            ),
            db=db,
            user=world["viewer"],
        )
    assert exc.value.status_code == 403
