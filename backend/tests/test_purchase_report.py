from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import purchase as purchase_service
from app.database import Base
from app.models.document import Document
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
        org = Organization(name="Report org")
        db.add(org)
        db.flush()
        project = Project(name="Report project", organization_id=org.id)
        other = Project(name="Other", organization_id=org.id)
        db.add_all([project, other])
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "other": other}
    engine.dispose()


def _make_doc(db, project_id, name="invoice.pdf"):
    doc = Document(project_id=project_id, name=name)
    db.add(doc)
    db.flush()
    return doc


def _make_entry(db, project_id, *, title="Счёт №1", counterparty="ООО Ромашка",
                status="planned", direction="outflow",
                planned=Decimal("100"), invoiced=Decimal("100"),
                actual=Decimal("0"), source_document_id=None,
                planned_date=date(2025, 1, 1)):
    entry = CashFlowEntry(
        project_id=project_id,
        title=title,
        counterparty=counterparty,
        planned_date=planned_date,
        status=status,
        direction=direction,
        planned_amount=planned,
        invoiced_amount=invoiced,
        actual_amount=actual,
        source_document_id=source_document_id,
    )
    db.add(entry)
    db.flush()
    return entry


def _make_item(db, project_id, name, unit="шт", qty_needed="10", status="need"):
    item = PurchaseItem(
        project_id=project_id,
        group_key=name.lower(),
        name=name,
        unit=unit,
        qty_needed=Decimal(qty_needed),
        status=status,
    )
    db.add(item)
    db.flush()
    return item


def _make_line(db, project_id, entry, item, qty, match_status="confirmed"):
    line = InvoiceLine(
        project_id=project_id,
        cash_flow_entry_id=entry.id,
        name=item.name,
        unit=item.unit,
        qty=Decimal(qty),
        purchase_item_id=item.id if match_status != "none" else None,
        match_status=match_status,
    )
    db.add(line)
    db.flush()
    return line


def test_report_paid_and_ordered(world):
    db = world["db"]
    project = world["project"]
    item = _make_item(db, project.id, "Щит", qty_needed="10")
    paid_entry = _make_entry(db, project.id, status="paid", title="Счёт оплачен")
    open_entry = _make_entry(db, project.id, status="planned", title="Счёт открыт")
    _make_line(db, project.id, paid_entry, item, "4")
    _make_line(db, project.id, open_entry, item, "3")
    db.commit()

    res = purchase_service.report(db, project.id)
    assert len(res["items"]) == 1
    row = res["items"][0]
    assert Decimal(row["needed"]) == 10
    assert Decimal(row["ordered"]) == 7
    assert Decimal(row["paid"]) == 4
    assert Decimal(row["left"]) == 3
    assert Decimal(row["to_pay"]) == 3
    assert len(row["invoices"]) == 2


def test_report_ignores_unconfirmed_and_counts_unmatched(world):
    db = world["db"]
    project = world["project"]
    item = _make_item(db, project.id, "Щит", qty_needed="10")
    entry = _make_entry(db, project.id, status="paid")
    _make_line(db, project.id, entry, item, "3", match_status="suggested")
    _make_line(db, project.id, entry, item, "2", match_status="none")
    _make_line(db, project.id, entry, item, "1", match_status="confirmed")
    db.commit()

    res = purchase_service.report(db, project.id)
    row = res["items"][0]
    assert Decimal(row["ordered"]) == 1
    assert Decimal(row["paid"]) == 1
    assert res["unmatched_lines"] == 2


def test_report_paid_by_actual_amount(world):
    db = world["db"]
    project = world["project"]
    item = _make_item(db, project.id, "Щит", qty_needed="10")
    entry = _make_entry(
        db, project.id, status="planned",
        invoiced=Decimal("100"), actual=Decimal("100"),
    )
    _make_line(db, project.id, entry, item, "5")
    part_entry = _make_entry(
        db, project.id, status="planned", title="Частично",
        invoiced=Decimal("100"), actual=Decimal("50"),
    )
    _make_line(db, project.id, part_entry, item, "2")
    db.commit()

    res = purchase_service.report(db, project.id)
    row = res["items"][0]
    assert Decimal(row["ordered"]) == 7
    assert Decimal(row["paid"]) == 5


def test_report_totals(world):
    db = world["db"]
    project = world["project"]
    full = _make_item(db, project.id, "Щит", qty_needed="5")
    partial = _make_item(db, project.id, "Кабель", qty_needed="10")
    empty = _make_item(db, project.id, "Лампа", qty_needed="0")
    entry = _make_entry(db, project.id, status="paid")
    _make_line(db, project.id, entry, full, "5")
    _make_line(db, project.id, entry, partial, "4")
    db.commit()

    res = purchase_service.report(db, project.id)
    totals = res["totals"]
    assert totals["positions"] == 3
    assert totals["fully_ordered"] == 1  # только full; empty (needed=0) не считается
    assert totals["fully_paid"] == 1  # только full
    assert Decimal(totals["needed_sum"]) == 15
    assert Decimal(totals["ordered_sum"]) == 9
    assert Decimal(totals["paid_sum"]) == 9
    assert empty.status == "need"


def test_invoices_for_matching_filters(world):
    db = world["db"]
    project = world["project"]
    item = _make_item(db, project.id, "Щит", qty_needed="10")
    doc = _make_doc(db, project.id)
    e1 = _make_entry(
        db, project.id, title="С документом",
        source_document_id=doc.id, planned_date=date(2025, 1, 2),
    )
    _make_line(db, project.id, e1, item, "1", match_status="confirmed")
    _make_line(db, project.id, e1, item, "2", match_status="suggested")
    _make_line(db, project.id, e1, item, "3", match_status="none")
    _make_entry(
        db, project.id, title="Без документа",
        source_document_id=None, planned_date=date(2025, 1, 3),
    )
    _make_entry(
        db, project.id, title="Входящий", direction="inflow",
        source_document_id=doc.id, planned_date=date(2025, 1, 4),
    )
    _make_entry(
        db, project.id, title="Отменён", status="cancelled",
        source_document_id=doc.id, planned_date=date(2025, 1, 5),
    )
    db.commit()

    rows = purchase_service.invoices_for_matching(db, project.id)
    assert [r["entry_id"] for r in rows] == [e1.id]
    assert rows[0]["lines"] == 3
    assert rows[0]["confirmed"] == 1
    assert rows[0]["suggested"] == 1
    assert rows[0]["paid"] is False


def test_api_report_and_invoices(world, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import purchase as purchase_api
    from app.core.auth import require_user
    from app.database import get_db

    db = world["db"]
    project = world["project"]
    other = world["other"]
    item = _make_item(db, project.id, "Щит", qty_needed="10")
    doc = _make_doc(db, project.id)
    entry = _make_entry(db, project.id, status="paid", source_document_id=doc.id)
    _make_line(db, project.id, entry, item, "4")
    _make_item(db, other.id, "Чужой", qty_needed="1")
    db.commit()

    fake_user = type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    app = FastAPI()
    app.include_router(purchase_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: fake_user
    client = TestClient(app)

    r = client.get(f"/projects/{project.id}/purchase/report")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "totals" in body and "unmatched_lines" in body
    assert len(body["items"]) == 1

    r = client.get(f"/projects/{project.id}/purchase/invoices")
    assert r.status_code == 200
    assert len(r.json()["invoices"]) == 1
    assert r.json()["invoices"][0]["entry_id"] == entry.id

    # Чужой проект не виден
    def viewer_scoped(_db, _user, pid, minimum="viewer"):
        if pid != project.id:
            raise HTTPException(status_code=404, detail="not found")
        return "viewer"

    monkeypatch.setattr(purchase_api, "require_project_role", viewer_scoped)
    assert client.get(f"/projects/{project.id}/purchase/report").status_code == 200
    assert client.get(f"/projects/{other.id}/purchase/report").status_code == 404
    assert client.get(f"/projects/{project.id}/purchase/invoices").status_code == 200

    # /purchase/report не перехватывается /{item_id}
    r = client.get(f"/projects/{project.id}/purchase/report")
    assert r.status_code == 200
    r = client.get(f"/projects/{project.id}/purchase")
    assert r.status_code == 200


def _physical_receipt(db, item, key, quantity, *, unit=None, project_id=None):
    from app.models.purchase import MaterialReceipt
    row = MaterialReceipt(project_id=project_id if project_id is not None else item.project_id,
                          purchase_item_id=item.id, operation_key=key,
                          qty=Decimal(quantity), unit=unit if unit is not None else item.unit)
    db.add(row)
    db.flush()
    return row


def test_received_totals_exact_groups_and_project_isolation(world):
    db, pid, other = world['db'], world['project'].id, world['other'].id
    item = _make_item(db, pid, 'Material', unit='м')
    second = _make_item(db, pid, 'Second', unit='шт')
    foreign = _make_item(db, other, 'Foreign', unit='м')
    _physical_receipt(db, item, 'one', '0.001')
    _physical_receipt(db, item, 'two', '0.002')
    _physical_receipt(db, item, 'historical-unit', '5', unit='кг')
    _physical_receipt(db, second, 'second', '2')
    _physical_receipt(db, foreign, 'foreign', '100')
    db.commit()
    result = purchase_service.received_totals(db, pid, row_budget=4)
    assert result['receipt_count'] == 4
    by_key = {(r['purchase_item_id'], r['unit']): r for r in result['materials']}
    assert by_key[(item.id, 'м')]['qty_received'] == '0.003'
    assert by_key[(item.id, 'м')]['receipt_count'] == 2
    assert by_key[(item.id, 'кг')]['qty_received'] == '5.000'
    assert by_key[(second.id, 'шт')]['qty_received'] == '2.000'
    assert len(by_key) == 3 and all(key[0] != foreign.id for key in by_key)
    keys = [(r['purchase_item_id'], r['unit']) for r in result['materials']]
    assert keys == sorted(keys)


def test_received_totals_does_not_infer_physical_arrival_from_finance(world):
    db, pid = world['db'], world['project'].id
    item = _make_item(db, pid, 'Paid material', status='paid')
    item.qty_ordered = Decimal('10')
    invoice = _make_entry(db, pid, actual=Decimal('100'), status='paid')
    _make_line(db, pid, invoice, item, '10')
    db.commit()
    assert purchase_service.received_totals(db, pid, row_budget=1) == {
        'project_id': pid, 'receipt_count': 0, 'materials': [],
    }


def test_received_totals_budget_boundary_and_readonly_sql(world):
    db, pid = world['db'], world['project'].id
    item = _make_item(db, pid, 'Material', unit='м')
    _physical_receipt(db, item, 'one', '1')
    _physical_receipt(db, item, 'two', '2')
    db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        assert purchase_service.received_totals(db, pid, row_budget=2)['materials'][0]['qty_received'] == '3.000'
        with pytest.raises(ValueError, match='exceeds row budget'):
            purchase_service.received_totals(db, pid, row_budget=1)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert statements and all(not sql.lstrip().lower().startswith(('insert', 'update', 'delete')) for sql in statements)
    receipt_queries = [sql.lower() for sql in statements if 'from material_receipts' in sql.lower()]
    assert len(receipt_queries) == 2
    assert all('limit' in sql and 'substr(' in sql and 'purchase_items.name' not in sql for sql in receipt_queries)


@pytest.mark.parametrize('budget', [0, -1, True, 1.5, '1', 10001])
def test_received_totals_invalid_budget(world, budget):
    with pytest.raises(ValueError):
        purchase_service.received_totals(world['db'], world['project'].id, row_budget=budget)


def test_received_totals_foreign_material_corruption_is_rejected(world):
    db, pid = world['db'], world['project'].id
    foreign = _make_item(db, world['other'].id, 'Foreign', unit='м')
    _physical_receipt(db, foreign, 'corrupt-link', '1', project_id=pid)
    db.commit()
    with pytest.raises(ValueError, match='ownership'):
        purchase_service.received_totals(db, pid, row_budget=1)


def test_received_totals_pending_caller_state_not_flushed_or_counted(world):
    from sqlalchemy import select
    from app.models.purchase import MaterialReceipt
    db, pid = world['db'], world['project'].id
    item = _make_item(db, pid, 'Material', unit='м')
    _physical_receipt(db, item, 'stored', '1')
    db.commit()
    item_id = item.id
    item.note = 'Pending caller change'
    pending = PurchaseItem(project_id=pid, group_key='pending', name='Pending', unit='м')
    pending_receipt = MaterialReceipt(project_id=pid, purchase_item_id=item_id, operation_key='pending', qty=Decimal('9'), unit='м')
    db.add_all([pending, pending_receipt])
    result = purchase_service.received_totals(db, pid, row_budget=1)
    assert result['receipt_count'] == 1 and result['materials'][0]['qty_received'] == '1.000'
    assert item in db.dirty and pending in db.new and pending_receipt in db.new
    assert pending.id is None and pending_receipt.id is None
    with db.no_autoflush:
        assert db.scalar(select(PurchaseItem.note).where(PurchaseItem.id == item_id)) is None


def test_received_totals_large_stored_unit_rejected(world):
    db, pid = world['db'], world['project'].id
    item = _make_item(db, pid, 'Material', unit='м')
    _physical_receipt(db, item, 'bad-unit', '1', unit='X' * 100000)
    db.commit()
    with pytest.raises(ValueError, match='unit'):
        purchase_service.received_totals(db, pid, row_budget=1)


def test_received_totals_unknown_project(world):
    with pytest.raises(LookupError):
        purchase_service.received_totals(world['db'], 999999, row_budget=1)


def test_received_totals_ignores_caller_decimal_precision(world):
    from decimal import localcontext
    db, pid = world['db'], world['project'].id
    item = _make_item(db, pid, 'Material', unit='м')
    _physical_receipt(db, item, 'one', '1.250')
    _physical_receipt(db, item, 'two', '2.005')
    db.commit()
    with localcontext() as context:
        context.prec = 2
        result = purchase_service.received_totals(db, pid, row_budget=2)
        assert result['materials'][0]['qty_received'] == '3.255'
        assert context.prec == 2
