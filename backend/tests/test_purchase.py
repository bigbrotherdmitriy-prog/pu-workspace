from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import purchase as purchase_service
from app.api.purchase import to_xlsx
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.purchase import MaterialReceipt, MaterialWriteoff, PurchaseItem
from app.models.rd import RdSpecItem, RdVolume
from app.models.work_log import WorkLogEntry


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
        org = Organization(name="Purchase org")
        db.add(org)
        db.flush()
        project = Project(name="Purchase project", organization_id=org.id)
        other = Project(name="Other", organization_id=org.id)
        db.add_all([project, other])
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "other": other}
    engine.dispose()


def _volume(db, project_id, title="Том 1", ext="v1"):
    vol = RdVolume(
        project_id=project_id,
        title=title,
        file_name=f"{title}.pdf",
        provider="google_drive",
        external_id=ext,
    )
    db.add(vol)
    db.flush()
    return vol


def _spec(db, project_id, volume_id, **kwargs):
    spec = RdSpecItem(
        project_id=project_id,
        volume_id=volume_id,
        status=kwargs.pop("status", "confirmed"),
        **kwargs,
    )
    db.add(spec)
    db.flush()
    return spec


def test_group_key_normalizes():
    a = purchase_service.group_key("Щит ЩР-УПП1 (… – аналогично)", None, "шт")
    b = purchase_service.group_key("щит щр-упп1", None, "шт")
    assert a == b


def test_rebuild_groups_and_sums(world):
    db = world["db"]
    project = world["project"]
    vol1 = _volume(db, project.id, "Том А", "va")
    vol2 = _volume(db, project.id, "Том Б", "vb")
    _spec(db, project.id, vol1.id, name="Кабель ВВГ", unit="м", qty=Decimal("10"))
    _spec(db, project.id, vol2.id, name="кабель ввг", unit="м", qty=Decimal("5"))
    _spec(db, project.id, vol1.id, name="Розетка", unit="шт", qty=Decimal("2"),
          status="proposed")
    _spec(db, project.id, vol1.id, name="Лампа", unit="шт", qty=Decimal("3"),
          status="rejected")
    db.commit()

    res = purchase_service.rebuild(db, project.id)
    assert res["items"] == 1
    assert res["created"] == 1
    assert res["updated"] == 0

    items = purchase_service.list_items(db, project.id)
    assert len(items) == 1
    assert Decimal(items[0]["qty_needed"]) == 15
    assert sorted(items[0]["volumes"]) == sorted(["Том А", "Том Б"])


def test_rebuild_preserves_status_and_removes_need(world):
    db = world["db"]
    project = world["project"]
    vol = _volume(db, project.id, "Том", "v")
    s1 = _spec(db, project.id, vol.id, name="Щит", unit="шт", qty=Decimal("1"))
    s2 = _spec(db, project.id, vol.id, name="Лампа", unit="шт", qty=Decimal("2"))
    db.commit()

    purchase_service.rebuild(db, project.id)
    items = {it["name"]: it for it in purchase_service.list_items(db, project.id)}
    assert set(items) == {"Щит", "Лампа"}

    purchase_service.set_status(db, project.id, items["Щит"]["id"], "ordered",
                                qty_ordered="1")
    purchase_service.rebuild(db, project.id)
    items = {it["name"]: it for it in purchase_service.list_items(db, project.id)}
    assert items["Щит"]["status"] == "ordered"
    assert Decimal(items["Щит"]["qty_ordered"]) == 1

    s1.status = "rejected"
    s2.status = "rejected"
    db.commit()
    res = purchase_service.rebuild(db, project.id)
    items = {it["name"]: it for it in purchase_service.list_items(db, project.id)}
    assert "Щит" in items
    assert "Лампа" not in items
    assert res["removed"] == 1


def test_set_status_rules(world):
    db = world["db"]
    project = world["project"]
    other = world["other"]
    vol = _volume(db, project.id, "Том", "v")
    _spec(db, project.id, vol.id, name="Щит", unit="шт", qty=Decimal("4"))
    db.commit()
    purchase_service.rebuild(db, project.id)
    item = purchase_service.list_items(db, project.id)[0]

    res = purchase_service.set_status(db, project.id, item["id"], "ordered")
    assert res["status"] == "ordered"
    assert Decimal(res["qty_ordered"]) == 4

    res = purchase_service.set_status(db, project.id, item["id"], "need")
    assert Decimal(res["qty_ordered"]) == 0

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        purchase_service.set_status(db, other.id, item["id"], "ordered")
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc2:
        purchase_service.set_status(db, project.id, item["id"], "bogus")
    assert exc2.value.status_code == 422


def test_to_xlsx_roundtrip(world):
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook
    from io import BytesIO

    items = [{
        "name": "Щит", "type_mark": "ЩР", "manufacturer": "M",
        "unit": "шт", "qty_needed": "2", "qty_ordered": "0",
        "qty_left": "2", "status": "need", "note": None,
    }]
    data = to_xlsx(items)
    assert isinstance(data, bytes)
    wb = load_workbook(BytesIO(data))
    ws = wb.active
    assert ws.title == "Закупка"
    headers = [c.value for c in ws[1]]
    assert headers[0] == "№"
    assert headers[1] == "Наименование"
    assert ws.max_row == 2
    assert ws.cell(row=2, column=9).value == "Нужно заказать"


def test_api_endpoints(world, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import purchase as purchase_api
    from app.core.auth import require_user
    from app.database import get_db

    db = world["db"]
    project = world["project"]
    vol = _volume(db, project.id, "Том", "v")
    _spec(db, project.id, vol.id, name="Щит", unit="шт", qty=Decimal("1"))
    db.commit()

    fake_user = type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    app = FastAPI()
    app.include_router(purchase_api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: fake_user
    client = TestClient(app)

    r = client.get(f"/projects/{project.id}/purchase")
    assert r.status_code == 200
    assert "items" in r.json() and "summary" in r.json()

    r = client.post(f"/projects/{project.id}/purchase/rebuild")
    assert r.status_code == 200
    assert r.json()["items"] == 1

    item_id = client.get(f"/projects/{project.id}/purchase").json()["items"][0]["id"]
    r = client.patch(f"/projects/{project.id}/purchase/{item_id}", json={"status": "ordered"})
    assert r.status_code == 200
    assert r.json()["status"] == "ordered"

    r = client.get(f"/projects/{project.id}/purchase/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    def viewer_only(_db, _user, _pid, minimum="viewer"):
        if minimum != "viewer":
            raise HTTPException(status_code=403, detail="Insufficient project access")
        return "viewer"

    monkeypatch.setattr(purchase_api, "require_project_role", viewer_only)
    assert client.get(f"/projects/{project.id}/purchase").status_code == 200
    assert client.post(f"/projects/{project.id}/purchase/rebuild").status_code == 403
    assert client.patch(f"/projects/{project.id}/purchase/{item_id}", json={"status": "paid"}).status_code == 403


# ---------------------------------------------------------------------------
# PR521 model constraint tests (synthetic SQLite, no HTTP, no service layer).
#
# These tests document ONLY what the DB schema enforces. They do NOT claim the
# models enforce: same-project linkage between project_id and purchase_item_id,
# non-negative aggregate on-hand quantities, immutability of posted history, or
# concurrent idempotence of operation_key under PostgreSQL. Those remain
# service-layer gates.
# ---------------------------------------------------------------------------


def _ledger_fixtures(db, project, other):
    """Create real Project/PurchaseItem/WorkLogEntry rows for ledger tests."""
    item = PurchaseItem(
        project_id=project.id,
        group_key="ledger-item",
        name="Кабель",
        unit="м",
        qty_needed=Decimal("10"),
    )
    other_item = PurchaseItem(
        project_id=other.id,
        group_key="ledger-item",
        name="Кабель",
        unit="м",
        qty_needed=Decimal("10"),
    )
    db.add_all([item, other_item])
    db.flush()
    entry = WorkLogEntry(
        project_id=project.id,
        work_date=__import__("datetime").date(2024, 1, 1),
        work_name="Монтаж",
        unit="м",
        qty=Decimal("1"),
    )
    db.add(entry)
    db.flush()
    db.commit()
    return item, other_item, entry


def _count(db, model):
    return db.execute(select(__import__("sqlalchemy").func.count()).select_from(model)).scalar_one()


def _expect_integrity(db, build):
    """Run build() inside a savepoint; assert IntegrityError; keep session usable."""
    before = {m: _count(db, m) for m in (MaterialReceipt, MaterialWriteoff)}
    sp = db.begin_nested()
    try:
        build()
        db.flush()
    except IntegrityError:
        sp.rollback()
    else:
        sp.rollback()
        pytest.fail("expected IntegrityError")
    after = {m: _count(db, m) for m in (MaterialReceipt, MaterialWriteoff)}
    assert before == after


def test_receipt_positive_decimal_persists(world):
    db = world["db"]
    item, _other_item, _entry = _ledger_fixtures(db, world["project"], world["other"])
    r = MaterialReceipt(
        project_id=world["project"].id,
        purchase_item_id=item.id,
        operation_key="rcpt-1",
        qty=Decimal("12.345"),
        unit="м",
    )
    db.add(r)
    db.commit()
    db.expire_all()
    loaded = db.get(MaterialReceipt, r.id)
    assert loaded is not None
    assert loaded.qty == Decimal("12.345")


def test_writeoff_positive_decimal_persists(world):
    db = world["db"]
    item, _other_item, entry = _ledger_fixtures(db, world["project"], world["other"])
    w = MaterialWriteoff(
        project_id=world["project"].id,
        purchase_item_id=item.id,
        work_log_entry_id=entry.id,
        operation_key="wo-1",
        qty=Decimal("3.500"),
        unit="м",
    )
    db.add(w)
    db.commit()
    db.expire_all()
    loaded = db.get(MaterialWriteoff, w.id)
    assert loaded is not None
    assert loaded.qty == Decimal("3.500")


def test_receipt_unique_project_operation_key(world):
    db = world["db"]
    item, _other_item, _entry = _ledger_fixtures(db, world["project"], world["other"])
    db.add(MaterialReceipt(
        project_id=world["project"].id, purchase_item_id=item.id,
        operation_key="dup", qty=Decimal("1"), unit="м",
    ))
    db.commit()

    def build():
        db.add(MaterialReceipt(
            project_id=world["project"].id, purchase_item_id=item.id,
            operation_key="dup", qty=Decimal("2"), unit="м",
        ))

    _expect_integrity(db, build)

    # Same key on a different project is accepted.
    db.add(MaterialReceipt(
        project_id=world["other"].id, purchase_item_id=item.id,
        operation_key="dup", qty=Decimal("2"), unit="м",
    ))
    db.commit()
    assert _count(db, MaterialReceipt) == 2


def test_writeoff_unique_project_operation_key(world):
    db = world["db"]
    item, _other_item, entry = _ledger_fixtures(db, world["project"], world["other"])
    db.add(MaterialWriteoff(
        project_id=world["project"].id, purchase_item_id=item.id,
        work_log_entry_id=entry.id, operation_key="dup", qty=Decimal("1"), unit="м",
    ))
    db.commit()

    def build():
        db.add(MaterialWriteoff(
            project_id=world["project"].id, purchase_item_id=item.id,
            work_log_entry_id=entry.id, operation_key="dup", qty=Decimal("2"), unit="м",
        ))

    _expect_integrity(db, build)

    db.add(MaterialWriteoff(
        project_id=world["other"].id, purchase_item_id=item.id,
        work_log_entry_id=entry.id, operation_key="dup", qty=Decimal("2"), unit="м",
    ))
    db.commit()
    assert _count(db, MaterialWriteoff) == 2


@pytest.mark.parametrize("qty", [Decimal("0"), Decimal("-1")])
def test_receipt_qty_check(world, qty):
    db = world["db"]
    item, _other_item, _entry = _ledger_fixtures(db, world["project"], world["other"])

    def build():
        db.add(MaterialReceipt(
            project_id=world["project"].id, purchase_item_id=item.id,
            operation_key=f"bad-{qty}", qty=qty, unit="м",
        ))

    _expect_integrity(db, build)


@pytest.mark.parametrize("qty", [Decimal("0"), Decimal("-1")])
def test_writeoff_qty_check(world, qty):
    db = world["db"]
    item, _other_item, entry = _ledger_fixtures(db, world["project"], world["other"])

    def build():
        db.add(MaterialWriteoff(
            project_id=world["project"].id, purchase_item_id=item.id,
            work_log_entry_id=entry.id, operation_key=f"bad-{qty}",
            qty=qty, unit="м",
        ))

    _expect_integrity(db, build)


def test_receipt_missing_fk_rejected(world):
    db = world["db"]
    _ledger_fixtures(db, world["project"], world["other"])

    def build():
        db.add(MaterialReceipt(
            project_id=world["project"].id, purchase_item_id=999999,
            operation_key="missing-fk", qty=Decimal("1"), unit="м",
        ))

    _expect_integrity(db, build)


def test_writeoff_missing_fk_rejected(world):
    db = world["db"]
    item, _other_item, _entry = _ledger_fixtures(db, world["project"], world["other"])

    def build():
        db.add(MaterialWriteoff(
            project_id=world["project"].id, purchase_item_id=item.id,
            work_log_entry_id=999999, operation_key="missing-fk",
            qty=Decimal("1"), unit="м",
        ))

    _expect_integrity(db, build)


def test_writeoff_one_reversal_per_original(world):
    db = world["db"]
    item, _other_item, entry = _ledger_fixtures(db, world["project"], world["other"])
    original = MaterialWriteoff(
        project_id=world["project"].id, purchase_item_id=item.id,
        work_log_entry_id=entry.id, operation_key="orig", qty=Decimal("5"), unit="м",
    )
    db.add(original)
    db.commit()

    db.add(MaterialWriteoff(
        project_id=world["project"].id, purchase_item_id=item.id,
        work_log_entry_id=entry.id, operation_key="rev-1", qty=Decimal("5"),
        unit="м", reverses_id=original.id,
    ))
    db.commit()

    def build():
        db.add(MaterialWriteoff(
            project_id=world["project"].id, purchase_item_id=item.id,
            work_log_entry_id=entry.id, operation_key="rev-2", qty=Decimal("5"),
            unit="м", reverses_id=original.id,
        ))

    _expect_integrity(db, build)


def test_writeoff_self_reversal_check(world):
    db = world["db"]
    item, _other_item, entry = _ledger_fixtures(db, world["project"], world["other"])
    w = MaterialWriteoff(
        project_id=world["project"].id, purchase_item_id=item.id,
        work_log_entry_id=entry.id, operation_key="self", qty=Decimal("1"), unit="м",
    )
    db.add(w)
    db.flush()
    w.reverses_id = w.id

    def build():
        db.flush()

    _expect_integrity(db, build)


def test_ledger_column_metadata(world):
    db = world["db"]
    for model in (MaterialReceipt, MaterialWriteoff):
        qty_col = model.__table__.c.qty
        assert qty_col.nullable is False
        assert qty_col.type.precision == 18
        assert qty_col.type.scale == 3
        unit_col = model.__table__.c.unit
        assert unit_col.nullable is False
        op_col = model.__table__.c.operation_key
        assert op_col.nullable is False
    assert MaterialWriteoff.__table__.c.reverses_id.nullable is True
    assert MaterialReceipt.__table__.c.source_document_ref.nullable is True
