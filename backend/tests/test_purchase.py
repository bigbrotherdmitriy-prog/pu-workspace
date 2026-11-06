from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import purchase as purchase_service
from app.api.purchase import to_xlsx
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.purchase import PurchaseItem
from app.models.rd import RdSpecItem, RdVolume


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
# PR531: formal inert migration tests for the material ledger proposal.
# These tests exercise ONLY the proposal DDL (material_receipts and
# material_writeoffs) against a synthetic SQLite in-memory schema. They do
# NOT use Base.metadata.create_all for the ledger tables, do NOT touch the
# legacy world fixture, and do NOT import app startup / env.py. They prove
# proposal DDL only, not the full 116-revision upgrade, PostgreSQL behaviour,
# no-negative aggregate logic, service layer, or production acceptance.
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger_world():
    import runpy
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.pool import StaticPool

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

    project_root = Path(__file__).resolve().parents[2]
    proposal_path = project_root / "docs" / "operations" / "proposals" / "material_ledger_migration.py"
    proposal = runpy.run_path(str(proposal_path))

    with engine.begin() as conn:
        # Synthetic parent tables only (no Base.metadata.create_all).
        conn.execute(text(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY)"
        ))
        conn.execute(text(
            "CREATE TABLE purchase_items (id INTEGER PRIMARY KEY)"
        ))
        conn.execute(text(
            "CREATE TABLE work_log_entries (id INTEGER PRIMARY KEY)"
        ))
        conn.execute(text("INSERT INTO projects (id) VALUES (1)"))
        conn.execute(text("INSERT INTO purchase_items (id) VALUES (1)"))
        conn.execute(text("INSERT INTO work_log_entries (id) VALUES (1)"))

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            proposal["upgrade"]()

    yield {"engine": engine, "proposal": proposal}
    engine.dispose()


def _table_info(conn, table):
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1]: r for r in rows}


def _index_list(conn, table):
    rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
    return {r[1]: r for r in rows}


def _index_info(conn, index):
    rows = conn.execute(text(f"PRAGMA index_info({index})")).fetchall()
    return [r[2] for r in rows]


def _fk_list(conn, table):
    rows = conn.execute(text(f"PRAGMA foreign_key_list({table})")).fetchall()
    return rows


def test_ledger_proposal_schema(ledger_world):
    from sqlalchemy import text

    engine = ledger_world["engine"]
    with engine.connect() as conn:
        # material_receipts columns / nullability / types
        cols = _table_info(conn, "material_receipts")
        assert set(cols) == {
            "id", "project_id", "purchase_item_id", "operation_key",
            "qty", "unit", "source_document_ref", "created_at",
        }
        assert cols["id"][5] == 1  # pk
        assert cols["project_id"][3] == 1  # not null
        assert cols["purchase_item_id"][3] == 1
        assert cols["operation_key"][3] == 1
        assert cols["qty"][3] == 1
        assert cols["unit"][3] == 1
        assert cols["source_document_ref"][3] == 0  # nullable
        assert cols["created_at"][3] == 1
        assert cols["qty"][2].upper().startswith("NUMERIC")
        assert "18" in cols["qty"][2] and "3" in cols["qty"][2]

        # material_writeoffs columns / nullability / types
        wcols = _table_info(conn, "material_writeoffs")
        assert set(wcols) == {
            "id", "project_id", "purchase_item_id", "work_log_entry_id",
            "operation_key", "qty", "unit", "reverses_id", "created_at",
        }
        assert wcols["id"][5] == 1
        assert wcols["project_id"][3] == 1
        assert wcols["purchase_item_id"][3] == 1
        assert wcols["work_log_entry_id"][3] == 1
        assert wcols["operation_key"][3] == 1
        assert wcols["qty"][3] == 1
        assert wcols["unit"][3] == 1
        assert wcols["reverses_id"][3] == 0  # nullable
        assert wcols["created_at"][3] == 1
        assert wcols["qty"][2].upper().startswith("NUMERIC")
        assert "18" in wcols["qty"][2] and "3" in wcols["qty"][2]

        # indexes
        r_idx = _index_list(conn, "material_receipts")
        assert "ix_material_receipts_project_id" in r_idx
        assert "ix_material_receipts_purchase_item_id" in r_idx
        assert _index_info(conn, "ix_material_receipts_project_id") == ["project_id"]
        assert _index_info(conn, "ix_material_receipts_purchase_item_id") == ["purchase_item_id"]

        w_idx = _index_list(conn, "material_writeoffs")
        assert "ix_material_writeoffs_project_id" in w_idx
        assert "ix_material_writeoffs_purchase_item_id" in w_idx
        assert "ix_material_writeoffs_work_log_entry_id" in w_idx
        assert _index_info(conn, "ix_material_writeoffs_project_id") == ["project_id"]
        assert _index_info(conn, "ix_material_writeoffs_purchase_item_id") == ["purchase_item_id"]
        assert _index_info(conn, "ix_material_writeoffs_work_log_entry_id") == ["work_log_entry_id"]

        # unique constraints (SQLite auto-indexes for UNIQUE)
        r_unique = {
            tuple(_index_info(conn, name))
            for name, row in r_idx.items() if row[2] == 1
        }
        assert ("project_id", "operation_key") in r_unique
        w_unique = {
            tuple(_index_info(conn, name))
            for name, row in w_idx.items() if row[2] == 1
        }
        assert ("project_id", "operation_key") in w_unique
        assert ("reverses_id",) in w_unique

        # check constraints present in DDL
        r_ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='material_receipts'"
        )).scalar()
        assert "ck_material_receipt_qty_positive" in r_ddl
        assert "qty > 0" in r_ddl
        w_ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='material_writeoffs'"
        )).scalar()
        assert "ck_material_writeoff_qty_positive" in w_ddl
        assert "qty > 0" in w_ddl
        assert "ck_material_writeoff_not_self" in w_ddl
        assert "reverses_id IS NULL OR reverses_id <> id" in w_ddl

        # foreign keys and ondelete targets
        r_fks = _fk_list(conn, "material_receipts")
        r_fk_map = {row[2]: row for row in r_fks}  # row[2] = referenced table
        assert "projects" in r_fk_map
        assert r_fk_map["projects"][6] == "CASCADE"
        assert "purchase_items" in r_fk_map
        assert r_fk_map["purchase_items"][6] == "RESTRICT"

        w_fks = _fk_list(conn, "material_writeoffs")
        w_fk_map = {row[2]: row for row in w_fks}
        assert "projects" in w_fk_map
        assert w_fk_map["projects"][6] == "CASCADE"
        assert "purchase_items" in w_fk_map
        assert w_fk_map["purchase_items"][6] == "RESTRICT"
        assert "work_log_entries" in w_fk_map
        assert w_fk_map["work_log_entries"][6] == "RESTRICT"
        assert "material_writeoffs" in w_fk_map
        assert w_fk_map["material_writeoffs"][6] == "RESTRICT"


def test_ledger_proposal_valid_roundtrip(ledger_world):
    from sqlalchemy import text

    engine = ledger_world["engine"]
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO material_receipts "
            "(project_id, purchase_item_id, operation_key, qty, unit) "
            "VALUES (1, 1, 'rcpt-1', 5.500, 'шт')"
        ))
        conn.execute(text(
            "INSERT INTO material_writeoffs "
            "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
            "VALUES (1, 1, 1, 'wo-1', 2.250, 'шт')"
        ))
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT qty, unit FROM material_receipts WHERE operation_key='rcpt-1'"
        )).fetchone()
        assert r is not None
        assert str(r[0]) in {"5.5", "5.500"}
        assert r[1] == "шт"
        w = conn.execute(text(
            "SELECT qty, unit FROM material_writeoffs WHERE operation_key='wo-1'"
        )).fetchone()
        assert w is not None
        assert str(w[0]) in {"2.25", "2.250"}
        assert w[1] == "шт"


def _expect_rejected(engine, sql, params=None):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(sql), params or {})
        except IntegrityError:
            trans.rollback()
            return True
        else:
            trans.rollback()
            return False


def test_ledger_proposal_rejections(ledger_world):
    from sqlalchemy import text

    engine = ledger_world["engine"]
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO material_receipts "
            "(project_id, purchase_item_id, operation_key, qty, unit) "
            "VALUES (1, 1, 'rcpt-ok', 1.000, 'шт')"
        ))
        conn.execute(text(
            "INSERT INTO material_writeoffs "
            "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
            "VALUES (1, 1, 1, 'wo-ok', 1.000, 'шт')"
        ))

    # unique (project_id, operation_key) on receipts
    assert _expect_rejected(
        engine,
        "INSERT INTO material_receipts "
        "(project_id, purchase_item_id, operation_key, qty, unit) "
        "VALUES (1, 1, 'rcpt-ok', 2.000, 'шт')",
    )
    # unique (project_id, operation_key) on writeoffs
    assert _expect_rejected(
        engine,
        "INSERT INTO material_writeoffs "
        "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
        "VALUES (1, 1, 1, 'wo-ok', 2.000, 'шт')",
    )
    # zero qty rejected
    assert _expect_rejected(
        engine,
        "INSERT INTO material_receipts "
        "(project_id, purchase_item_id, operation_key, qty, unit) "
        "VALUES (1, 1, 'rcpt-zero', 0, 'шт')",
    )
    assert _expect_rejected(
        engine,
        "INSERT INTO material_writeoffs "
        "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
        "VALUES (1, 1, 1, 'wo-zero', 0, 'шт')",
    )
    # negative qty rejected
    assert _expect_rejected(
        engine,
        "INSERT INTO material_receipts "
        "(project_id, purchase_item_id, operation_key, qty, unit) "
        "VALUES (1, 1, 'rcpt-neg', -1.000, 'шт')",
    )
    assert _expect_rejected(
        engine,
        "INSERT INTO material_writeoffs "
        "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
        "VALUES (1, 1, 1, 'wo-neg', -1.000, 'шт')",
    )
    # missing FK targets rejected
    assert _expect_rejected(
        engine,
        "INSERT INTO material_receipts "
        "(project_id, purchase_item_id, operation_key, qty, unit) "
        "VALUES (999, 1, 'rcpt-badproj', 1.000, 'шт')",
    )
    assert _expect_rejected(
        engine,
        "INSERT INTO material_receipts "
        "(project_id, purchase_item_id, operation_key, qty, unit) "
        "VALUES (1, 999, 'rcpt-baditem', 1.000, 'шт')",
    )
    assert _expect_rejected(
        engine,
        "INSERT INTO material_writeoffs "
        "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
        "VALUES (1, 1, 999, 'wo-badwle', 1.000, 'шт')",
    )

    # original rows and parent seeds unchanged after rejections
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM material_receipts")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM material_writeoffs")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM projects")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM purchase_items")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM work_log_entries")).scalar() == 1


def test_ledger_proposal_reversal_rules(ledger_world):
    from sqlalchemy import text

    engine = ledger_world["engine"]
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO material_writeoffs "
            "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit) "
            "VALUES (1, 1, 1, 'wo-orig', 3.000, 'шт')"
        ))
        orig_id = conn.execute(text(
            "SELECT id FROM material_writeoffs WHERE operation_key='wo-orig'"
        )).scalar()
        conn.execute(text(
            "INSERT INTO material_writeoffs "
            "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit, reverses_id) "
            "VALUES (1, 1, 1, 'wo-rev', 3.000, 'шт', :rid)"
        ), {"rid": orig_id})

    # self-reversal rejected (reverses_id = id)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(
                "INSERT INTO material_writeoffs "
                "(id, project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit, reverses_id) "
                "VALUES (100, 1, 1, 1, 'wo-self', 1.000, 'шт', 100)"
            ))
            trans.rollback()
            self_rejected = False
        except Exception:
            trans.rollback()
            self_rejected = True
    assert self_rejected

    # duplicate reversal of the same original rejected (unique reverses_id)
    assert _expect_rejected(
        engine,
        "INSERT INTO material_writeoffs "
        "(project_id, purchase_item_id, work_log_entry_id, operation_key, qty, unit, reverses_id) "
        "VALUES (1, 1, 1, 'wo-rev2', 3.000, 'шт', :rid)",
        {"rid": orig_id},
    )

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM material_writeoffs")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM projects")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM purchase_items")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM work_log_entries")).scalar() == 1


def test_ledger_proposal_downgrade_refuses(ledger_world):
    from sqlalchemy import text

    engine = ledger_world["engine"]
    proposal = ledger_world["proposal"]
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO material_receipts "
            "(project_id, purchase_item_id, operation_key, qty, unit) "
            "VALUES (1, 1, 'rcpt-keep', 1.000, 'шт')"
        ))

    with pytest.raises(RuntimeError):
        proposal["downgrade"]()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM material_receipts")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM material_writeoffs")).scalar() == 0
