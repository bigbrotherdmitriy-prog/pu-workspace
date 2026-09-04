from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register all mapped tables
from app.api.dashboard import project_dashboard
from app.api.organizations_contracts import analyze_contract
from app.database import Base
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.management import Obligation
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def test_contract_analysis_keeps_local_document_provenance_in_dashboard():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        organization = Organization(name="Тестовая организация")
        user = User(name="Руководитель", email="owner@example.test")
        db.add_all([organization, user])
        db.flush()
        project = Project(name="Тестовый проект", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        document = Document(
            project_id=project.id,
            name="Договор без внешнего идентификатора.pdf",
            source="local_upload",
            status="analyzed",
        )
        db.add(document)
        db.flush()
        db.add(DocumentVersion(
            document_id=document.id,
            version_number=1,
            content=(
                "Подрядчик обязан предоставить акт не позднее 01.01.2020. "
                "Просрочка может привести к существенному штрафу."
            ),
        ))
        contract = Contract(
            project_id=project.id,
            number="ТЕСТ-1",
            title="Тестовый договор",
            status="active",
            source_document_id=document.id,
        )
        db.add(contract)
        db.commit()

        analyzed = analyze_contract(project.id, contract.id, db, user)
        dashboard = project_dashboard(project.id, db, user)

        obligation = db.scalar(select(Obligation).where(Obligation.contract_id == contract.id))
        source_row = next(row for row in dashboard["documents"] if row["document_id"] == document.id)
        assert analyzed["created"]["tasks"] == 1
        assert analyzed["created"]["risks"] == 1
        assert obligation is not None
        assert source_row["source_id"] == f"document:{document.id}"
        assert source_row["tasks"] == 1
        assert source_row["risks"] == 1
        assert dashboard["summary"]["attention"] >= 3
