from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import invoice_lines as il_service
from app.database import Base
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import CashFlowEntry
from app.models.invoice_line import InvoiceLine
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.purchase import PurchaseItem


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Invoice org")
        db.add(org)
        db.flush()
        project = Project(name="Invoice project", organization_id=org.id)
        other = Project(name="Other", organization_id=org.id)
        db.add_all([project, other])
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "other": other}
    engine.dispose()


def _make_doc(db, project_id, text="Счёт", name="invoice.pdf"):
    doc = Document(
        project_id=project_id,
        name=name,
    )
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        document_id=doc.id,
        version_number=1,
        content=text,
    )
    db.add(ver)
    db.flush()
    return doc, ver


def _make_entry(db, project_id, title="Счёт №1", counterparty="ООО Ромашка",
                source_document_id=None):
    from datetime import date
    entry = CashFlowEntry(
        project_id=project_id,
        title=title,
        counterparty=counterparty,
        planned_date=date(2025, 1, 1),
        status="planned",
        direction="outflow",
        planned_amount=Decimal("0"),
        invoiced_amount=Decimal("0"),
        actual_amount=Decimal("0"),
        source_document_id=source_document_id,
    )
    db.add(entry)
    db.flush()
    return entry


def _make_item(db, project_id, name, unit="шт", qty_needed="10"):
    item = PurchaseItem(
        project_id=project_id,
        group_key=name.lower(),
        name=name,
        unit=unit,
        qty_needed=Decimal(qty_needed),
        status="need",
    )
    db.add(item)
    db.flush()
    return item


def _stub_spec(items):
    def _fake(_text):
        return items, "test"
    return _fake


def test_similarity_high_and_low():
    high = il_service.similarity(
        "Щит ЩР-УПП1 6 шт", "щит щр-упп1", "шт", "шт",
    )
    assert high >= 0.5
    low = il_service.similarity("Кабель ВВГ", "Розетка", "м", "шт")
    assert low < 0.5


def test_extract_creates_lines_and_suggestions(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    doc, _ = _make_doc(db, project.id, "Счёт 123")
    entry = _make_entry(db, project.id, source_document_id=doc.id)
    _make_item(db, project.id, "Щит ЩР-УПП1", unit="шт")
    db.commit()

    monkeypatch.setattr(
        il_service, "specification_from_text",
        _stub_spec([
            {"name": "Щит ЩР-УПП1 6 шт", "unit": "шт", "qty": Decimal("6")},
            {"name": "Кабель ВВГ 3x2.5", "unit": "м", "qty": Decimal("100")},
            {"name": None, "unit": "шт", "qty": None},
        ]),
    )

    res = il_service.extract_lines(db, project.id, entry.id)
    assert res["lines"] == 2
    assert res["suggested"] >= 1

    rows = il_service.list_lines(db, project.id, entry_id=entry.id)
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert "Щит ЩР-УПП1 6 шт" in names
    suggested = [r for r in rows if r["match_status"] == "suggested"]
    assert suggested


def test_extract_requires_document(world, monkeypatch):
    from fastapi import HTTPException
    db = world["db"]
    project = world["project"]
    entry = _make_entry(db, project.id, source_document_id=None)
    db.commit()

    monkeypatch.setattr(il_service, "specification_from_text", _stub_spec([]))

    with pytest.raises(HTTPException) as exc:
        il_service.extract_lines(db, project.id, entry.id)
    assert exc.value.status_code == 422


def test_reextract_keeps_confirmed(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    doc, _ = _make_doc(db, project.id, "Счёт")
    entry = _make_entry(db, project.id, source_document_id=doc.id)
    item = _make_item(db, project.id, "Щит ЩР-УПП1")
    db.commit()

    monkeypatch.setattr(
        il_service, "specification_from_text",
        _stub_spec([
            {"name": "Щит ЩР-УПП1", "unit": "шт", "qty": Decimal("6")},
        ]),
    )
    il_service.extract_lines(db, project.id, entry.id)
    rows = il_service.list_lines(db, project.id, entry_id=entry.id)
    line_id = rows[0]["id"]
    il_service.set_match(db, project.id, line_id, item.id)

    confirmed = db.execute(
        select(InvoiceLine).where(InvoiceLine.id == line_id)
    ).scalar_one()
    assert confirmed.match_status == "confirmed"

    monkeypatch.setattr(
        il_service, "specification_from_text",
        _stub_spec([
            {"name": "Щит ЩР-УПП1", "unit": "шт", "qty": Decimal("6")},
        ]),
    )
    il_service.extract_lines(db, project.id, entry.id)

    rows = il_service.list_lines(db, project.id, entry_id=entry.id)
    confirmed_rows = [r for r in rows if r["match_status"] == "confirmed"]
    assert len(confirmed_rows) == 1
    assert confirmed_rows[0]["id"] == line_id


def test_set_match_recomputes_ordered(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    doc, _ = _make_doc(db, project.id, "Счёт")
    entry = _make_entry(db, project.id, source_document_id=doc.id)
    item = _make_item(db, project.id, "Щит ЩР-УПП1", qty_needed="10")
    db.commit()

    monkeypatch.setattr(
        il_service, "specification_from_text",
        _stub_spec([
            {"name": "Щит ЩР-УПП1", "unit": "шт", "qty": Decimal("6")},
        ]),
    )
    il_service.extract_lines(db, project.id, entry.id)
    rows = il_service.list_lines(db, project.id, entry_id=entry.id)
    line_id = rows[0]["id"]

    il_service.set_match(db, project.id, line_id, item.id)
    db.refresh(item)
    assert Decimal(item.qty_ordered) == Decimal("6")
    assert item.status == "ordered"

    il_service.set_match(db, project.id, line_id, None)
    db.refresh(item)
    assert Decimal(item.qty_ordered) == Decimal("0")
    assert item.status == "need"


def test_foreign_project_404(world, monkeypatch):
    from fastapi import HTTPException
    db = world["db"]
    project = world["project"]
    other = world["other"]
    doc, _ = _make_doc(db, project.id, "Счёт")
    entry = _make_entry(db, project.id, source_document_id=doc.id)
    item = _make_item(db, project.id, "Щит")
    db.commit()

    monkeypatch.setattr(
        il_service, "specification_from_text",
        _stub_spec([{"name": "Щит", "unit": "шт", "qty": Decimal("1")}]),
    )

    with pytest.raises(HTTPException) as exc:
        il_service.extract_lines(db, other.id, entry.id)
    assert exc.value.status_code == 404

    il_service.extract_lines(db, project.id, entry.id)
    rows = il_service.list_lines(db, project.id, entry_id=entry.id)
    line_id = rows[0]["id"]

    with pytest.raises(HTTPException) as exc2:
        il_service.set_match(db, other.id, line_id, item.id)
    assert exc2.value.status_code == 404

    with pytest.raises(HTTPException) as exc3:
        il_service.set_match(db, project.id, line_id, item.id + 1000)
    assert exc3.value.status_code == 404


def test_api_endpoints(world, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import invoice_lines as il_api
    from app.core.auth import require_user
    from app.database import get_db

    db = world["db"]
    project = world["project"]
    doc, _ = _make_doc(db, project.id, "Счёт")
    entry = _make_entry(db, project.id, source_document_id=doc.id)
    item = _make_item(db, project.id, "Щит ЩР-УПП1")
    db.commit()

    monkeypatch.setattr(
        il_service, "specification_from_text",
        _stub_spec([{"name": "Щит ЩР-УПП1", "unit": "шт", "qty": Decimal("6")}]),
    )

    fake_user = type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    app = FastAPI()
    app.include_router(il_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: fake_user
    client = TestClient(app)

    r = client.post(
        f"/projects/{project.id}/cash-flow/{entry.id}/invoice-lines/extract"
    )
    assert r.status_code == 200
    assert r.json()["lines"] == 1

    r = client.get(f"/projects/{project.id}/invoice-lines")
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert len(lines) == 1
    line_id = lines[0]["id"]

    r = client.post(
        f"/projects/{project.id}/invoice-lines/{line_id}/match",
        json={"purchase_item_id": item.id},
    )
    assert r.status_code == 200
    assert r.json()["match_status"] == "confirmed"

    def viewer_only(_db, _user, _pid, minimum="viewer"):
        if minimum != "viewer":
            raise HTTPException(status_code=403, detail="Insufficient project access")
        return "viewer"

    monkeypatch.setattr(il_api, "require_project_role", viewer_only)
    assert client.get(f"/projects/{project.id}/invoice-lines").status_code == 200
    assert client.post(
        f"/projects/{project.id}/cash-flow/{entry.id}/invoice-lines/extract"
    ).status_code == 403
    assert client.post(
        f"/projects/{project.id}/invoice-lines/{line_id}/match",
        json={"purchase_item_id": item.id},
    ).status_code == 403
