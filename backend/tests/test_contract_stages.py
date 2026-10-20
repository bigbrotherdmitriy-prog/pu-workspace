from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.contract_document_link import ContractDocumentLink
from app.models.contract_stage import ContractStage
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app import contract_stages as cs
from app.api import contract_stages as api


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
        user = User(name="Manager", email="stg-m@example.test", is_admin=False)
        viewer = User(name="Viewer", email="stg-v@example.test", is_admin=False)
        db.add_all([org, user, viewer])
        db.flush()
        project = Project(name="Project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
        contract = Contract(project_id=project.id, number="1", title="Contract")
        db.add(contract)
        db.commit()
        yield {"db": db, "project": project, "contract": contract, "user": user, "viewer": viewer}
    engine.dispose()


def _add_doc(db, project_id, name, content, contract_id=None):
    doc = Document(project_id=project_id, name=name)
    db.add(doc)
    db.flush()
    doc.current_version = 1
    db.add(DocumentVersion(document_id=doc.id, version_number=1, content=content))
    if contract_id is not None:
        db.add(ContractDocumentLink(project_id=project_id, contract_id=contract_id, document_id=doc.id))
    db.commit()
    return doc


def test_parse_stages_regex_multi_object():
    text = (
        "Этапы и стоимость выполнения работ ДУБНА Начало Окончание Стоимость этапа "
        "1 этап 1. Разработка решения 01.04.2026 30.04.2026 23 446 972,13 "
        "2 этап 1. Подготовка инфраструктуры 01.04.2026 30.05.2026 9 466 536,00 "
        "ГОРОДЕЦ Начало Окончание Стоимость этапа "
        "1-й этап 1. Разработка решения 12.01.2026 31.01.2026 22 000 000,00"
    )
    stages = cs.parse_stages_regex(text.replace(" этапа ", " этапа\n").replace(" 1 этап", "\n1 этап").replace(" 2 этап", "\n2 этап").replace(" 1-й этап", "\n1-й этап"))
    assert len(stages) == 3
    objects = [s["object_name"] for s in stages]
    assert objects[0] is not None and "ДУБНА" in objects[0]
    assert objects[1] is not None and "ДУБНА" in objects[1]
    assert objects[2] is not None and "ГОРОДЕЦ" in objects[2]
    assert [s["stage_number"] for s in stages] == [1, 2, 1]
    assert stages[0]["amount"] == Decimal("23446972.13")
    assert stages[1]["amount"] == Decimal("9466536.00")
    assert stages[2]["amount"] == Decimal("22000000.00")
    assert stages[0]["planned_start"] == date(2026, 4, 1)
    assert stages[0]["planned_finish"] == date(2026, 4, 30)
    assert stages[2]["planned_start"] == date(2026, 1, 12)
    assert stages[2]["planned_finish"] == date(2026, 1, 31)


def test_parse_stages_regex_without_objects():
    text = (
        "1 этап Монтаж 01.02.2026 28.02.2026 100 000,00\n"
        "2 этап ПНР 01.03.2026 31.03.2026 50 000,00"
    )
    stages = cs.parse_stages_regex(text)
    assert len(stages) == 2
    assert all(s["object_name"] is None for s in stages)
    assert stages[0]["amount"] == Decimal("100000.00")
    assert stages[1]["amount"] == Decimal("50000.00")


def test_extract_falls_back_to_regex_and_no_duplicates(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    contract = world["contract"]
    _add_doc(
        db, project.id, "Договор",
        "1 этап Монтаж 01.02.2026 28.02.2026 100 000,00\n"
        "2 этап ПНР 01.03.2026 31.03.2026 50 000,00",
        contract_id=contract.id,
    )
    monkeypatch.setattr(cs, "extract_stages_ai", lambda texts: None)

    r1 = cs.extract_contract_stages(db, project.id, contract)
    db.commit()
    assert r1["method"] == "regex"
    assert len(db.query(ContractStage).filter_by(contract_id=contract.id).all()) == 2

    r2 = cs.extract_contract_stages(db, project.id, contract)
    db.commit()
    assert len(db.query(ContractStage).filter_by(contract_id=contract.id).all()) == 2

    # Confirm one, then re-extract -> confirmed must remain
    db.query(ContractStage).filter_by(contract_id=contract.id).update({"status": "confirmed"})
    db.commit()
    r3 = cs.extract_contract_stages(db, project.id, contract)
    db.commit()
    remaining = db.query(ContractStage).filter_by(contract_id=contract.id).all()
    assert len(remaining) == 2
    assert all(s.status == "confirmed" for s in remaining)


def test_ai_rejects_amount_not_in_quote(monkeypatch):
    texts = [(1, "doc", "1 этап Работы 01.02.2026 28.02.2026 100 000,00")]

    class _Gem:
        @staticmethod
        def deepseek_configured():
            return True

        @staticmethod
        def _try_deepseek(system, user, schema):
            return {"stages": [
                {
                    "object_name": None,
                    "stage_number": 1,
                    "title": "Работы",
                    "planned_start": "2026-02-01",
                    "planned_finish": "2026-02-28",
                    "amount": 999999,
                    "evidence_quote": "1 этап Работы 01.02.2026 28.02.2026 100 000,00",
                }
            ]}

    import app.gemini_analysis as gem
    monkeypatch.setattr(gem, "deepseek_configured", _Gem.deepseek_configured)
    monkeypatch.setattr(gem, "_try_deepseek", _Gem._try_deepseek)
    result = cs.extract_stages_ai(texts)
    assert result is None


def test_get_returns_by_object_and_total(world):
    db = world["db"]
    project = world["project"]
    contract = world["contract"]
    _add_doc(
        db, project.id, "Договор",
        "1 этап Монтаж 01.02.2026 28.02.2026 100 000,00\n"
        "2 этап ПНР 01.03.2026 31.03.2026 50 000,00",
        contract_id=contract.id,
    )
    cs.extract_contract_stages(db, project.id, contract)
    db.commit()

    result = api.list_contract_stages(
        project_id=project.id, contract_id=contract.id, db=db, user=world["viewer"],
    )
    assert result["total"] == "150000.00"
    assert result["by_object"][""] == "150000.00"
    assert len(result["items"]) == 2


def test_printed_numbering_gap_is_renumbered_in_order_with_warning():
    from app.contract_stages import parse_stages_regex, renumber_in_order

    text = (
        "ГОРОДЕЦ Начало Окончание Стоимость этапа "
        "3-й этап Подготовка 01.02.2026 30.04.2026 54 336 733,87 "
        "5-й этап Поставка 01.03.2026 30.06.2026 81 790 938,22 "
        "6-й этап Поставка 2 02.03.2026 30.06.2026 81 838 748,22"
    )
    stages, warnings = renumber_in_order(parse_stages_regex(text))
    assert [s["stage_number"] for s in stages] == [1, 2, 3]
    assert warnings == ["В документе (ГОРОДЕЦ) нумерация этапов не по порядку: «3-й» … «6-й» приняты как 1–3."]
