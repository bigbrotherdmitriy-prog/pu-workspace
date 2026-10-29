import io
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import id_orders as svc
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Ord org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "o": o}
    engine.dispose()


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_catalog_has_the_requested_orders():
    kinds = {c["kind"] for c in svc.catalog()}
    assert {"labor_safety", "hot_work", "electrical_safety", "fire_safety", "works_manager", "work_at_height"} <= kinds
    assert all(c["certificate"] for c in svc.catalog())


def test_order_is_incomplete_until_name_and_certificate_copy(world):
    db, pid = world["db"], world["p"].id
    o = svc.create_order(db, pid, {"kind": "hot_work", "position": "Прораб", "org_name": "ООО Монтаж", "object_name": "ЦОД Дубна"})
    assert o["complete"] is False and o["missing"] == ["ФИО ответственного", "копия удостоверения"]
    o = svc.update_order(db, pid, o["id"], {"person_name": "Иванов И.И.", "number": "12", "order_date": "2026-10-20"})
    assert o["missing"] == ["копия удостоверения"]
    o = svc.add_certificate(db, pid, o["id"], "уд.pdf", "application/pdf", b"%PDF-1.4 x")
    assert o["complete"] is True and o["certificates"][0]["filename"] == "уд.pdf"
    o = svc.delete_certificate(db, pid, o["id"], o["certificates"][0]["id"])
    assert o["complete"] is False


def test_validation_and_isolation(world):
    db, pid = world["db"], world["p"].id
    with pytest.raises(ValueError):
        svc.create_order(db, pid, {"kind": "unknown"})
    o = svc.create_order(db, pid, {"kind": "fire_safety"})
    with pytest.raises(ValueError):
        svc.add_certificate(db, pid, o["id"], "a.exe", "application/x-msdownload", b"x")
    with pytest.raises(ValueError):
        svc.add_certificate(db, pid, o["id"], "a.pdf", "application/pdf", b"")
    with pytest.raises(ValueError):
        svc.add_certificate(db, pid, o["id"], "a.pdf", "application/pdf", b"x" * (svc.MAX_FILE_BYTES + 1))
    with pytest.raises(LookupError):
        svc.update_order(db, world["o"].id, o["id"], {"number": "1"})
    with pytest.raises(LookupError):
        svc.delete_order(db, world["o"].id, o["id"])


def test_docx_contains_name_duties_and_certificate_clause(world):
    db, pid = world["db"], world["p"].id
    o = svc.create_order(db, pid, {"kind": "electrical_safety", "person_name": "Петров П.П.", "position": "инженера",
                                   "org_name": "ООО Монтаж", "object_name": "ЦОД Дубна", "number": "5", "order_date": "2026-10-20",
                                   "signer_name": "Сидоров С.С.", "signer_position": "Генеральный директор", "city": "Дубна"})
    from app.models.id_orders import IdOrder

    xml = _docx_text(svc.render_docx(db.get(IdOrder, o["id"])))
    assert "работника: Петров П.П., инженера" in xml
    for needle in ("ПРИКАЗ", "№ 5", "Петров П.П.", "электробезопасность", "группа по электробезопасности", "октября", "С приказом ознакомлен", "копия удостоверения"):
        assert needle.lower() in xml.lower(), needle


def test_package_requires_certificates_then_contains_orders_and_copies(world):
    db, pid = world["db"], world["p"].id
    with pytest.raises(ValueError):
        svc.package_zip(db, pid)
    o = svc.create_order(db, pid, {"kind": "labor_safety", "person_name": "Иванов И.И."})
    with pytest.raises(ValueError) as e:
        svc.package_zip(db, pid)
    assert "копия удостоверения" in str(e.value)
    svc.add_certificate(db, pid, o["id"], "уд.jpg", "image/jpeg", b"\xff\xd8\xff data")
    names = zipfile.ZipFile(io.BytesIO(svc.package_zip(db, pid))).namelist()
    assert any(n.endswith("Приказ.docx") for n in names) and any("Удостоверение_1" in n for n in names)


def test_api(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import id_orders as api
    from app.core.auth import require_user
    from app.database import get_db

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    c = TestClient(app)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/id-orders", json={"kind": "hot_work", "person_name": "Иванов И.И."})
    assert r.status_code == 200
    oid = r.json()["id"]
    assert c.post(f"/projects/{pid}/id-orders", json={"kind": "nope"}).status_code == 422
    assert c.get(f"/projects/{pid}/id-orders/package.zip").status_code == 409
    r = c.post(f"/projects/{pid}/id-orders/{oid}/certificate", params={"name": "уд.pdf"}, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
    assert r.status_code == 200 and r.json()["complete"] is True
    fid = r.json()["certificates"][0]["id"]
    assert c.get(f"/projects/{pid}/id-orders/{oid}/certificate/{fid}").content == b"%PDF-1.4"
    assert c.get(f"/projects/{pid}/id-orders/{oid}/order.docx").content[:2] == b"PK"
    assert c.get(f"/projects/{pid}/id-orders/package.zip").status_code == 200
    assert c.get(f"/projects/{pid}/id-orders").json()["orders"][0]["complete"] is True
    assert c.delete(f"/projects/{pid}/id-orders/{oid}/certificate/{fid}").json()["complete"] is False
    assert c.delete(f"/projects/{pid}/id-orders/{oid}").json() == {"deleted": oid}
    assert c.get(f"/projects/{world['o'].id}/id-orders/{oid}/order.docx").status_code == 404
