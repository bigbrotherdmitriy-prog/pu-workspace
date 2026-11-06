from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import onboarding
from app.database import Base
from app.models.document import Document
from app.models.equipment import EquipmentUnit
from app.models.execution_finance import CashFlowEntry, ScheduleBaseline, ScheduleItem
from app.models.executive_docs import IdSource
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.purchase import PurchaseItem
from app.models.rd import RdSpecItem, RdVolume
from app.models.user import User
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


# --- Real SQLite onboarding: all ten steps through actual models ---

ALL_KEYS = [
    "contract", "documents", "schedule", "dds", "rd",
    "rd_confirm", "purchase", "equipment", "id", "work_log",
]

# Explicit key -> screen contract. Ten steps intentionally share eight screens:
# schedule+dds both live on "ГПР и ДДС"; rd+rd_confirm both on "Рабочая документация".
EXPECTED_SCREENS = {
    "contract": "Договоры",
    "documents": "Документы",
    "schedule": "ГПР и ДДС",
    "dds": "ГПР и ДДС",
    "rd": "Рабочая документация",
    "rd_confirm": "Рабочая документация",
    "purchase": "Закупка",
    "equipment": "Оборудование",
    "id": "Исполнительная документация",
    "work_log": "Журнал работ",
}


def _seed_step(db, pid, key, user_id, tag):
    """Create the real record(s) that satisfy one onboarding step."""
    if key == "contract":
        db.add(Contract(project_id=pid, number=f"C-{tag}", title="Договор"))
    elif key == "documents":
        db.add(Document(project_id=pid, name=f"doc-{tag}.pdf"))
    elif key == "schedule":
        base = ScheduleBaseline(project_id=pid, created_by_user_id=user_id,
                                name=f"ГПР-{tag}", version=1)
        db.add(base)
        db.flush()
        db.add(ScheduleItem(project_id=pid, baseline_id=base.id, title=f"Этап-{tag}"))
    elif key == "dds":
        db.add(CashFlowEntry(project_id=pid, direction="inflow", title=f"Платёж-{tag}",
                             planned_date=date(2026, 1, 1), planned_amount=Decimal("1")))
    elif key == "rd":
        db.add(RdVolume(project_id=pid, title=f"Том-{tag}", file_name=f"rd-{tag}.pdf",
                        provider="pu_storage"))
    elif key == "rd_confirm":
        vol = RdVolume(project_id=pid, title=f"ТомC-{tag}", file_name=f"rdc-{tag}.pdf",
                       provider="pu_storage")
        db.add(vol)
        db.flush()
        db.add(RdSpecItem(project_id=pid, volume_id=vol.id, name=f"Позиция-{tag}",
                          status="confirmed"))
    elif key == "purchase":
        db.add(PurchaseItem(project_id=pid, group_key=f"g-{tag}", name=f"Закупка-{tag}"))
    elif key == "equipment":
        db.add(EquipmentUnit(project_id=pid, name=f"Оборудование-{tag}"))
    elif key == "id":
        db.add(IdSource(project_id=pid, object_name=f"Объект-{tag}",
                        folder_id=f"folder-{tag}", provider="pu_storage"))
    elif key == "work_log":
        db.add(WorkLogEntry(project_id=pid, work_date=date(2026, 2, 1),
                            work_name=f"Работа-{tag}"))
    else:  # pragma: no cover
        raise AssertionError(key)


def _done_keys(res):
    return {i["key"] for i in res["items"] if i["done"]}


def test_service_full_onboarding_all_ten_steps(world):
    db, pid = world["db"], world["p"].id
    user = User(name="Onb User", email="onb@example.com", is_admin=False)
    db.add(user)
    db.flush()
    for key in ALL_KEYS:
        _seed_step(db, pid, key, user.id, "full")
    db.commit()

    res = onboarding.steps(db, pid)
    assert len(res["items"]) == 10
    assert [i["key"] for i in res["items"]] == ALL_KEYS
    assert len({i["key"] for i in res["items"]}) == 10
    assert {i["key"]: i["screen"] for i in res["items"]} == EXPECTED_SCREENS
    assert _done_keys(res) == set(ALL_KEYS)
    assert res["done"] == 10 and res["total"] == 10 and res["percent"] == 100
    assert res["next"] is None

    with Session(db.get_bind()) as fresh:
        again = onboarding.steps(fresh, pid)
    assert again["done"] == 10 and again["percent"] == 100 and again["next"] is None
    assert _done_keys(again) == set(ALL_KEYS)


def test_service_full_onboarding_worklog_removal_and_foreign_isolation(world):
    db, pid, oid = world["db"], world["p"].id, world["o"].id
    user = User(name="Onb User2", email="onb2@example.com", is_admin=False)
    db.add(user)
    db.flush()
    for key in ALL_KEYS:
        _seed_step(db, pid, key, user.id, "rm")
    # Foreign project fully onboarded: must not leak into local counts.
    for key in ALL_KEYS:
        _seed_step(db, oid, key, user.id, "foreign")
    db.commit()

    assert onboarding.steps(db, pid)["done"] == 10

    db.query(WorkLogEntry).filter(WorkLogEntry.project_id == pid).delete()
    db.commit()

    with Session(db.get_bind()) as fresh:
        res = onboarding.steps(fresh, pid)
    assert res["done"] == 9 and res["total"] == 10 and res["percent"] == 90
    assert res["next"] == "work_log"
    assert _done_keys(res) == set(ALL_KEYS) - {"work_log"}

    with Session(db.get_bind()) as fresh:
        foreign = onboarding.steps(fresh, oid)
    assert foreign["done"] == 10 and foreign["percent"] == 100 and foreign["next"] is None


def test_service_full_onboarding_incremental_local_steps(world):
    db, pid, oid = world["db"], world["p"].id, world["o"].id
    user = User(name="Onb User3", email="onb3@example.com", is_admin=False)
    db.add(user)
    db.flush()
    # Foreign project fully populated first: local project must stay empty.
    for key in ALL_KEYS:
        _seed_step(db, oid, key, user.id, "f3")
    db.commit()

    res = onboarding.steps(db, pid)
    assert res["done"] == 0 and res["percent"] == 0 and res["next"] == "contract"
    assert _done_keys(res) == set()

    expected_done = 0
    for idx, key in enumerate(ALL_KEYS):
        _seed_step(db, pid, key, user.id, f"inc{idx}")
        db.commit()
        expected_done += 1
        res = onboarding.steps(db, pid)
        assert res["done"] == expected_done
        assert res["percent"] == round(expected_done * 100 / 10)
        assert _done_keys(res) == set(ALL_KEYS[: idx + 1])
        expected_next = ALL_KEYS[idx + 1] if idx + 1 < len(ALL_KEYS) else None
        assert res["next"] == expected_next

    assert res["done"] == 10 and res["percent"] == 100 and res["next"] is None
