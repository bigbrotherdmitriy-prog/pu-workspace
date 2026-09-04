from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest
from fastapi import HTTPException

import app.models  # noqa: F401
from app.api.organizations_contracts import (
    ContractCreate,
    ContractLinkUpdate,
    create_contract,
    initialize_contract_control,
    update_contract_links,
)
from app.database import Base
from app.models.document import Document
from app.models.execution_finance import ScheduleBaseline
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def test_document_contract_control_chain_is_safe_and_idempotent():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Acceptance")
        user = User(name="Owner", email="owner@acceptance.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="Объект", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        document = Document(
            project_id=project.id,
            name="Договор ГК-01.pdf",
            external_id="source-contract-1",
            source="google_drive",
            status="analyzed",
            current_version=1,
        )
        db.add(document); db.commit()

        contract_payload = create_contract(
            project.id,
            ContractCreate(number="ГК-01", title="Основной договор", counterparty="Заказчик"),
            db,
            user,
        )
        contract_id = contract_payload["id"]
        linked = update_contract_links(
            project.id,
            contract_id,
            ContractLinkUpdate(source_document_id=document.id),
            db,
            user,
        )
        first = initialize_contract_control(project.id, contract_id, db, user)
        second = initialize_contract_control(project.id, contract_id, db, user)

        baselines = list(db.scalars(select(ScheduleBaseline).where(
            ScheduleBaseline.project_id == project.id,
            ScheduleBaseline.contract_id == contract_id,
        )))
        assert linked["source_document_id"] == document.id
        assert first["baseline_id"] == second["baseline_id"]
        assert first["created"] is False  # contract creation already prepared the GPR anchor
        assert second["created"] is False
        assert len(baselines) == 1
        assert document.name == "Договор ГК-01.pdf"


def test_existing_contract_can_be_reclassified_and_linked_atomically():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Tree")
        user = User(name="Owner", email="tree@test.local", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="Дерево", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner")); db.commit()
        prime = create_contract(project.id, ContractCreate(number="ГК", title="Генподряд", contract_kind="prime_reference"), db, user)
        legacy = create_contract(project.id, ContractCreate(number="СП", title="Наш договор", contract_kind="customer"), db, user)
        linked = update_contract_links(project.id, legacy["id"], ContractLinkUpdate(
            contract_kind="revenue_subcontract", parent_contract_id=prime["id"],
        ), db, user)
        assert linked["contract_kind"] == "revenue_subcontract"
        assert linked["parent_contract_id"] == prime["id"]
        with pytest.raises(HTTPException, match="самим собой"):
            update_contract_links(project.id, legacy["id"], ContractLinkUpdate(
                contract_kind="downstream_subcontract", parent_contract_id=legacy["id"],
            ), db, user)
