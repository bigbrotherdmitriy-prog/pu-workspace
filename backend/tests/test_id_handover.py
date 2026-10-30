import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import id_forms as api
from app.core.auth import require_user
from app.database import Base, get_db
from app.models.executive_docs import IdParticipant, IdRequirement
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSpecItem, RdVolume


def _text(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="ООО Монтаж")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        db.add(p)
        db.flush()
        v = RdVolume(project_id=p.id, object_name="ДУБНА", section="ЭМ", title="ЭМ", file_name="em.pdf", provider="pu_storage", external_id="1")
        db.add(v)
        db.flush()
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Щит ЩР-1", type_mark="ЩРн-24", unit="шт", status="confirmed"))
        db.add(RdSpecItem(volume_id=v.id, project_id=p.id, name="Лишнее", status="rejected"))
        db.add(IdRequirement(project_id=p.id, object_name="ДУБНА", title="Журнал прокладки кабелей", doc_kind="journal", source="kit"))
        db.add(IdParticipant(project_id=p.id, object_name="ДУБНА", role="customer_control", full_name="Петров П.П.", position="инженер"))
        db.commit()
        yield {"db": db, "p": p}
    engine.dispose()


def _client(world):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    return TestClient(app)


def test_defaults_come_from_project(world):
    h = _client(world).get(f"/projects/{world['p'].id}/executive-docs/handover/defaults", params={"object_name": "ДУБНА"}).json()
    assert h["contractor"] == "ООО Монтаж" and h["customer_rep"] == "Петров П.П., инженер" and h["object_name"] == "ДУБНА"


def test_equipment_register_from_rd(world):
    c = _client(world)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/executive-docs/equipment-register.docx", json={"object_name": "ДУБНА", "header": {"object_name": "ДУБНА"}})
    assert r.status_code == 200
    xml = _text(r.content)
    assert "ЩРн-24" in xml and "Лишнее" not in xml
    assert c.post(f"/projects/{pid}/executive-docs/equipment-register.xlsx", json={"object_name": "ДУБНА"}).status_code == 200
    assert c.post(f"/projects/{pid}/executive-docs/equipment-register.pdf", json={}).status_code == 404


def test_id_register_lists_kit_with_status(world):
    c = _client(world)
    r = c.post(f"/projects/{world['p'].id}/executive-docs/id-register.docx", json={"object_name": "ДУБНА"})
    assert r.status_code == 200
    xml = _text(r.content)
    assert "Журнал прокладки кабелей" in xml and "нет" in xml
    assert c.post(f"/projects/{world['p'].id}/executive-docs/id-register.docx", json={}).status_code == 422


def test_trial_and_commissioning_acts(world):
    c = _client(world)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/executive-docs/trial-act.docx", json={
        "header": {"act_no": "7", "start": "01.11.2026 09:00", "end": "04.11.2026 09:00", "hours": "72"},
        "items": [{"name": "Электроснабжение", "mode": "нагрузка 60 %", "result": "pass"}]})
    assert r.status_code == 200 and "72" in _text(r.content) and "Электроснабжение" in _text(r.content)
    assert c.post(f"/projects/{pid}/executive-docs/trial-act.docx", json={"items": []}).status_code == 422
    r = c.post(f"/projects/{pid}/executive-docs/commissioning-act.docx", json={"header": {"scope": "ЭМ"}})
    assert r.status_code == 200 and "ввода в эксплуатацию" in _text(r.content)
