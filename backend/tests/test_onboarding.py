from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import onboarding
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSpecItem, RdVolume
from app.models.work_log import WorkLogEntry


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Onb org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "o": o}
    engine.dispose()


def test_empty_project_starts_with_the_contract(world):
    res = onboarding.steps(world["db"], world["p"].id)
    assert res["done"] == 0 and res["percent"] == 0 and res["total"] == 10
    assert res["next"] == "contract"
    assert [i["screen"] for i in res["items"]][:2] == ["Договоры", "Документы"]


def test_progress_follows_real_data_and_ignores_other_projects(world):
    from datetime import date

    db, pid = world["db"], world["p"].id
    vol = RdVolume(project_id=pid, title="V", file_name="v.pdf", provider="google_drive")
    other_vol = RdVolume(project_id=world["o"].id, title="V2", file_name="v2.pdf", provider="google_drive")
    db.add_all([vol, other_vol])
    db.flush()
    db.add(RdSpecItem(volume_id=vol.id, project_id=pid, name="Насос", unit="шт", qty=Decimal("1"), status="proposed"))
    db.add(WorkLogEntry(project_id=world["o"].id, work_date=date(2026, 10, 1), work_name="Чужая"))
    db.commit()
    res = onboarding.steps(db, pid)
    done = {i["key"] for i in res["items"] if i["done"]}
    assert done == {"rd"}  # позиции есть, но не подтверждены; журнал чужого проекта не в счёт
    assert res["next"] == "contract"
    db.query(RdSpecItem).update({"status": "confirmed"})
    db.commit()
    assert {i["key"] for i in onboarding.steps(db, pid)["items"] if i["done"]} == {"rd", "rd_confirm"}


def test_api(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import onboarding as api
    from app.core.auth import require_user
    from app.database import get_db

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    r = TestClient(app).get(f"/projects/{world['p'].id}/onboarding")
    assert r.status_code == 200 and r.json()["total"] == 10 and r.json()["next"] == "contract"
