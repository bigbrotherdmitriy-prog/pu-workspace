import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import equipment_test_act as eta
from app import work_log as wl
from app import work_log_form
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project


def _xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        return zf.read("word/document.xml").decode("utf-8")


def test_equipment_test_act_content_and_validation():
    header = {"act_no": "7", "object_name": "ЦОД Дубна", "date": "2026-10-20", "customer_rep": "Иванов И.И."}
    xml = _xml(eta.render(header, [
        {"name": "Насос Grundfos CR 5", "type_mark": "CR 5-8", "serial": "SN-1", "program": "Пробный пуск 2 ч", "values": "ток 4.1 А", "result": "pass"},
    ]))
    assert "АКТ № 7" in xml and "ЦОД Дубна" in xml and "Насос Grundfos CR 5" in xml
    assert "выдержало испытание" in xml and "может быть принято для комплексного опробования" in xml
    assert "20" in xml and "октября" in xml
    bad = _xml(eta.render({}, [{"name": "Щит", "result": "fail"}]))
    assert "не выдержало испытание" in bad and "подлежат устранению" in bad
    with pytest.raises(ValueError):
        eta.render({}, [])
    with pytest.raises(ValueError):
        eta.render({}, [{"name": "X", "result": "maybe"}])
    with pytest.raises(ValueError):
        eta.render({}, [{"type_mark": "только марка"}])


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Form org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        db.add(p)
        db.flush()
        db.commit()
        yield {"db": db, "p": p}
    engine.dispose()


def test_work_log_form_has_sections_and_entries(world):
    db, pid = world["db"], world["p"].id
    crew = wl.add_crew(db, pid, {"name": "Бригада 1", "foreman": "Петров П.П.", "members": "Сидоров"})
    wl.add_entry(db, pid, 1, {"work_date": "2026-10-20", "work_name": "Прокладка кабеля", "qty": "40", "unit": "м",
                              "location": "Этаж 2", "weather": "+5, дождь", "crew_id": crew["id"], "hours": "8"})
    db.commit()
    xml = _xml(work_log_form.render(db, pid, {"object_name": "ЦОД Дубна", "journal_no": "1"}))
    for needle in ("ОБЩИЙ ЖУРНАЛ РАБОТ", "ЦОД Дубна", "Раздел 1.", "Раздел 3.", "Раздел 4.", "Раздел 5.",
                   "Прокладка кабеля", "20.10.2026", "+5, дождь", "Петров П.П.", "бригада: Бригада 1", "часов: 8.00"):
        assert needle in xml, needle
    assert 'w:orient="landscape"' in xml


def test_api(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import id_forms as api
    from app.core.auth import require_user
    from app.database import get_db

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    c = TestClient(app)
    pid = world["p"].id
    r = c.post(f"/projects/{pid}/executive-docs/equipment-test-act.docx", json={"header": {}, "items": [{"name": "Щит", "result": "pass"}]})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert c.post(f"/projects/{pid}/executive-docs/equipment-test-act.docx", json={"header": {}, "items": []}).status_code == 422
    r = c.post(f"/projects/{pid}/work-log/form.docx", json={"header": {"object_name": "О"}})
    assert r.status_code == 200 and r.content[:2] == b"PK"
