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


def _add_scope_data(db, p):
    v2 = RdVolume(project_id=p.id, object_name="ВТОРОЙ", section="ЭМ", title="ЭМ-2", file_name="em2.pdf", provider="pu_storage", external_id="2")
    db.add(v2)
    db.flush()
    db.add(RdSpecItem(volume_id=v2.id, project_id=p.id, name="Кабель КВ-2", type_mark="ВВГ", unit="м", status="confirmed"))
    db.add(RdSpecItem(volume_id=v2.id, project_id=p.id, name="Отклонено-2", status="rejected"))
    org2 = Organization(name="ООО Чужой")
    db.add(org2)
    db.flush()
    p2 = Project(name="P2", organization_id=org2.id)
    db.add(p2)
    db.flush()
    v3 = RdVolume(project_id=p2.id, object_name="ДУБНА", section="ЭМ", title="ЭМ-3", file_name="em3.pdf", provider="pu_storage", external_id="3")
    db.add(v3)
    db.flush()
    db.add(RdSpecItem(volume_id=v3.id, project_id=p2.id, name="Чужой Щит", type_mark="ЧУЖ", unit="шт", status="confirmed"))
    db.commit()
    return p2


def test_service_object_scope_known_and_second_isolated(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    first = equipment_rows(db, p.id, "ДУБНА")
    second = equipment_rows(db, p.id, "ВТОРОЙ")
    assert [r["name"] for r in first] == ["Щит ЩР-1"]
    assert [r["name"] for r in second] == ["Кабель КВ-2"]


def test_service_object_scope_unknown_returns_empty(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    assert equipment_rows(db, p.id, "SYNTHETIC-NO-SUCH-OBJECT") == []


def test_service_object_scope_projectwide_for_none_and_empty(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    for scope in (None, ""):
        names = [r["name"] for r in equipment_rows(db, p.id, scope)]
        assert names == ["Щит ЩР-1", "Кабель КВ-2"]
        assert "Лишнее" not in names and "Отклонено-2" not in names and "Чужой Щит" not in names


def test_service_object_scope_reads_do_not_mutate(world):
    from app.id_handover import equipment_rows

    db, p = world["db"], world["p"]
    _add_scope_data(db, p)
    before = (db.query(RdVolume).count(), db.query(RdSpecItem).count())
    equipment_rows(db, p.id, "ДУБНА")
    equipment_rows(db, p.id, "SYNTHETIC-NO-SUCH-OBJECT")
    equipment_rows(db, p.id, None)
    equipment_rows(db, p.id, "")
    after = (db.query(RdVolume).count(), db.query(RdSpecItem).count())
    assert before == after
