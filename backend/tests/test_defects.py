from datetime import date, timedelta
from io import BytesIO

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import defects as svc
from app.database import Base
from app.models.defects import Defect
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdVolume, RdVolumeVersion

JPEG = b"\xff\xd8\xff\xe0 fake jpeg"


def _fk_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Def org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "o": o}
    engine.dispose()


@pytest.fixture
def fk_world():
    engine = _fk_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="FK org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        db.add(p)
        db.flush()
        vol = RdVolume(project_id=p.id, title="V", file_name="v.pdf", provider="local")
        db.add(vol)
        db.flush()
        ver = RdVolumeVersion(
            volume_id=vol.id, content_sha256="a" * 64, size_bytes=10,
            file_name="v.pdf", mime_type="application/pdf",
        )
        db.add(ver)
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "vol": vol, "ver": ver}
    engine.dispose()


@pytest.fixture
def bind_world():
    """FK-enabled world with two projects, each owning a volume+version."""
    engine = _fk_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Bind org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        vol_p = RdVolume(project_id=p.id, title="VP", file_name="vp.pdf", provider="local")
        vol_o = RdVolume(project_id=o.id, title="VO", file_name="vo.pdf", provider="local")
        db.add_all([vol_p, vol_o])
        db.flush()
        ver_p = RdVolumeVersion(
            volume_id=vol_p.id, content_sha256="c" * 64, size_bytes=11,
            file_name="vp.pdf", mime_type="application/pdf",
        )
        ver_o = RdVolumeVersion(
            volume_id=vol_o.id, content_sha256="d" * 64, size_bytes=12,
            file_name="vo.pdf", mime_type="application/pdf",
        )
        db.add_all([ver_p, ver_o])
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "o": o, "vol_p": vol_p, "vol_o": vol_o, "ver_p": ver_p, "ver_o": ver_o}
    engine.dispose()


def _defect(db, pid, **kw):
    d = Defect(project_id=pid, number=kw.pop("number", 1), title=kw.pop("title", "T"), **kw)
    db.add(d)
    db.flush()
    return d


def _snapshot(db, defect_id):
    db.expire_all()
    d = db.get(Defect, defect_id)
    return {
        "id": d.id,
        "project_id": d.project_id,
        "number": d.number,
        "title": d.title,
        "status": d.status,
        "rd_version_id": d.rd_version_id,
    }


def test_unbound_defect_allowed(fk_world):
    db, pid = fk_world["db"], fk_world["p"].id
    d = _defect(db, pid)
    db.commit()
    assert d.rd_version_id is None


def test_bind_persists_exact_id_across_new_version(fk_world):
    db, pid, ver, vol = fk_world["db"], fk_world["p"].id, fk_world["ver"], fk_world["vol"]
    d = _defect(db, pid, rd_version_id=ver.id)
    db.commit()
    bound_id = ver.id
    new_ver = RdVolumeVersion(
        volume_id=vol.id, content_sha256="b" * 64, size_bytes=20,
        file_name="v2.pdf", mime_type="application/pdf",
    )
    db.add(new_ver)
    db.commit()
    assert new_ver.id != bound_id
    db.expire_all()
    assert db.get(Defect, d.id).rd_version_id == bound_id


def test_referenced_version_deletion_rejected(fk_world):
    db, pid, ver = fk_world["db"], fk_world["p"].id, fk_world["ver"]
    _defect(db, pid, rd_version_id=ver.id)
    db.commit()
    with pytest.raises(IntegrityError):
        db.delete(ver)
        db.flush()
    db.rollback()
    assert db.get(RdVolumeVersion, ver.id) is not None


def test_nonexistent_version_rejected(fk_world):
    db, pid = fk_world["db"], fk_world["p"].id
    with pytest.raises(IntegrityError):
        _defect(db, pid, rd_version_id=999999)
    db.rollback()
    assert db.query(Defect).count() == 0


def test_numbers_are_per_project_and_validation(world):
    db, pid = world["db"], world["p"].id
    a = svc.create_defect(db, pid, None, {"title": "Трещина в стяжке", "location": "Этаж 2", "priority": "high"})
    b = svc.create_defect(db, pid, None, {"title": "Нет заземления"})
    c = svc.create_defect(db, world["o"].id, None, {"title": "Другой проект"})
    assert (a["number"], b["number"], c["number"]) == (1, 2, 1)
    assert a["status"] == "open" and a["priority"] == "high"
    with pytest.raises(ValueError):
        svc.create_defect(db, pid, None, {"title": "  "})
    with pytest.raises(ValueError):
        svc.create_defect(db, pid, None, {"title": "x", "priority": "urgent"})
    with pytest.raises(ValueError):
        svc.create_defect(db, pid, None, {"title": "x", "due_date": "завтра"})


def test_status_flow_and_fix_needs_evidence(world):
    db, pid = world["db"], world["p"].id
    d = svc.create_defect(db, pid, None, {"title": "Не закреплён лоток"})
    with pytest.raises(ValueError):
        svc.change_status(db, pid, None, d["id"], "accepted", None)  # из «Открыто» сразу в «Принято» нельзя
    svc.change_status(db, pid, None, d["id"], "in_progress", "Взяли в работу")
    with pytest.raises(ValueError):
        svc.change_status(db, pid, None, d["id"], "fixed", " ")  # нужен результат: фото или комментарий
    svc.add_photo(db, pid, d["id"], "after", "после.jpg", "image/jpeg", JPEG)
    fixed = svc.change_status(db, pid, None, d["id"], "fixed", None)
    assert fixed["status"] == "fixed"
    accepted = svc.change_status(db, pid, None, d["id"], "accepted", "Проверил")
    assert accepted["status"] == "accepted" and accepted["closed_at"]
    assert [c["status_change"] for c in accepted["comments"]] == ["in_progress", "fixed", "accepted"]
    reopened = svc.change_status(db, pid, None, d["id"], "open", "Опять отвалился")
    assert reopened["status"] == "open" and reopened["closed_at"] is None
    with pytest.raises(ValueError):
        svc.change_status(db, pid, None, d["id"], "open", None)


def test_photos_rules_and_isolation(world):
    db, pid = world["db"], world["p"].id
    d = svc.create_defect(db, pid, None, {"title": "Т"})
    res = svc.add_photo(db, pid, d["id"], "before", "до.jpg", "image/jpeg; charset=x", JPEG)
    assert res["photos"][0]["kind"] == "before" and res["photos"][0]["size_bytes"] == len(JPEG)
    for args in (("before", "a.gif", "image/gif", JPEG), ("before", "a.jpg", "image/jpeg", b""), ("maybe", "a.jpg", "image/jpeg", JPEG)):
        with pytest.raises(ValueError):
            svc.add_photo(db, pid, d["id"], *args)
    with pytest.raises(ValueError):
        svc.add_photo(db, pid, d["id"], "before", "big.jpg", "image/jpeg", b"x" * (svc.MAX_PHOTO_BYTES + 1))
    for i in range(svc.MAX_PHOTOS - 1):
        svc.add_photo(db, pid, d["id"], "before", f"{i}.jpg", "image/jpeg", JPEG)
    with pytest.raises(ValueError):
        svc.add_photo(db, pid, d["id"], "before", "last.jpg", "image/jpeg", JPEG)
    with pytest.raises(LookupError):
        svc.add_photo(db, world["o"].id, d["id"], "before", "x.jpg", "image/jpeg", JPEG)
    photo_id = svc.get_defect(db, pid, d["id"])["photos"][0]["id"]
    assert svc.get_photo(db, pid, d["id"], photo_id).data == JPEG
    with pytest.raises(LookupError):
        svc.get_photo(db, world["o"].id, d["id"], photo_id)
    assert len(svc.delete_photo(db, pid, d["id"], photo_id)["photos"]) == svc.MAX_PHOTOS - 1


def test_list_filters_counts_overdue_and_export(world):
    from openpyxl import load_workbook

    db, pid = world["db"], world["p"].id
    old = (date.today() - timedelta(days=3)).isoformat()
    a = svc.create_defect(db, pid, None, {"title": "Просроченное", "due_date": old, "location": "Этаж 3"})
    svc.create_defect(db, pid, None, {"title": "Свежее"})
    svc.change_status(db, pid, None, a["id"], "in_progress", None)
    res = svc.list_defects(db, pid)
    assert res["counts"]["open"] == 1 and res["counts"]["in_progress"] == 1 and res["overdue"] == 1
    assert [d["title"] for d in svc.list_defects(db, pid, status="in_progress")["defects"]] == ["Просроченное"]
    assert [d["title"] for d in svc.list_defects(db, pid, q="ЭТАЖ 3")["defects"]] == ["Просроченное"]
    ws = load_workbook(BytesIO(svc.to_xlsx(db, pid)))["Замечания"]
    assert ws["A1"].value == "№" and ws.max_row == 3 and ws["J2"].value == "да"
    svc.update_defect(db, pid, a["id"], {"responsible": "Иванов", "due_date": None, "title": "Новое имя"})
    assert svc.get_defect(db, pid, a["id"])["responsible"] == "Иванов"
    with pytest.raises(ValueError):
        svc.update_defect(db, pid, a["id"], {"title": " "})
    svc.delete_defect(db, pid, a["id"])
    assert svc.list_defects(db, pid)["counts"]["in_progress"] == 0
    with pytest.raises(LookupError):
        svc.get_defect(db, pid, a["id"])


def test_api(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import defects as api
    from app.core.auth import require_user
    from app.database import get_db

    db, pid = world["db"], world["p"].id
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": None, "is_active": True, "is_admin": True})()
    c = TestClient(app)
    r = c.post(f"/projects/{pid}/defects", json={"title": "Течь", "location": "Подвал"})
    assert r.status_code == 200
    did = r.json()["id"]
    assert c.post(f"/projects/{pid}/defects", json={"title": " "}).status_code == 422
    r = c.post(f"/projects/{pid}/defects/{did}/photos", params={"name": "a.jpg"}, content=JPEG, headers={"content-type": "image/jpeg"})
    assert r.status_code == 200
    photo = r.json()["photos"][0]["id"]
    assert c.get(f"/projects/{pid}/defects/{did}/photos/{photo}").content == JPEG
    assert c.post(f"/projects/{pid}/defects/{did}/photos", content=b"x", headers={"content-type": "text/plain"}).status_code == 422
    assert c.post(f"/projects/{pid}/defects/{did}/comments", json={"text": "Сфотографировал"}).json()["comments"][0]["text"] == "Сфотографировал"
    assert c.post(f"/projects/{pid}/defects/{did}/status", json={"status": "accepted"}).status_code == 422
    assert c.post(f"/projects/{pid}/defects/{did}/status", json={"status": "in_progress"}).json()["status"] == "in_progress"
    assert c.get(f"/projects/{pid}/defects").json()["counts"]["in_progress"] == 1
    assert c.get(f"/projects/{pid}/defects/export.xlsx").content[:2] == b"PK"
    assert c.get(f"/projects/{world['o'].id}/defects/{did}").status_code == 404
    assert c.delete(f"/projects/{pid}/defects/{did}").json() == {"deleted": did}


# --- DS13C: direct service tests for bind_rd_version (implemented in DS13B) ---
# These tests exercise the service function directly against a real FK-enabled
# SQLite session. They do not use the API, network, providers or production data.
# Thread concurrency is not claimed here; an atomic bind guard is a follow-up
# requirement before full acceptance.


def test_bind_initial_same_project_persists(bind_world):
    db, pid, ver = bind_world["db"], bind_world["p"].id, bind_world["ver_p"]
    d = _defect(db, pid)
    db.commit()
    before = _snapshot(db, d.id)
    assert before["rd_version_id"] is None
    out = svc.bind_rd_version(db, pid, d.id, ver.id)
    assert out["id"] == d.id
    assert out["rd_version_id"] == ver.id
    db.commit()
    after = _snapshot(db, d.id)
    assert after["rd_version_id"] == ver.id
    assert after["project_id"] == before["project_id"]
    assert after["number"] == before["number"]
    assert after["title"] == before["title"]
    assert after["status"] == before["status"]


def test_bind_same_version_repeat_no_change(bind_world):
    db, pid, ver = bind_world["db"], bind_world["p"].id, bind_world["ver_p"]
    d = _defect(db, pid)
    db.commit()
    first = svc.bind_rd_version(db, pid, d.id, ver.id)
    db.commit()
    snap = _snapshot(db, d.id)
    second = svc.bind_rd_version(db, pid, d.id, ver.id)
    db.commit()
    assert first["rd_version_id"] == ver.id
    assert second["rd_version_id"] == ver.id
    assert _snapshot(db, d.id) == snap


def test_bind_different_existing_version_rejected(bind_world):
    db, pid, vol_p, ver = bind_world["db"], bind_world["p"].id, bind_world["vol_p"], bind_world["ver_p"]
    other = RdVolumeVersion(
        volume_id=vol_p.id, content_sha256="e" * 64, size_bytes=13,
        file_name="vp2.pdf", mime_type="application/pdf",
    )
    db.add(other)
    db.flush()
    d = _defect(db, pid, rd_version_id=ver.id)
    db.commit()
    snap = _snapshot(db, d.id)
    assert snap["rd_version_id"] == ver.id
    with pytest.raises(ValueError):
        svc.bind_rd_version(db, pid, d.id, other.id)
    db.rollback()
    assert _snapshot(db, d.id) == snap
    assert _snapshot(db, d.id)["rd_version_id"] == ver.id


def test_bind_foreign_project_version_rejected(bind_world):
    db, pid, ver_o = bind_world["db"], bind_world["p"].id, bind_world["ver_o"]
    d = _defect(db, pid)
    db.commit()
    snap = _snapshot(db, d.id)
    with pytest.raises(LookupError):
        svc.bind_rd_version(db, pid, d.id, ver_o.id)
    db.rollback()
    assert _snapshot(db, d.id) == snap
    assert _snapshot(db, d.id)["rd_version_id"] is None


def test_bind_missing_version_same_lookup_error(bind_world):
    db, pid = bind_world["db"], bind_world["p"].id
    d = _defect(db, pid)
    db.commit()
    snap = _snapshot(db, d.id)
    with pytest.raises(LookupError):
        svc.bind_rd_version(db, pid, d.id, 999999)
    db.rollback()
    assert _snapshot(db, d.id) == snap
    assert _snapshot(db, d.id)["rd_version_id"] is None


def test_bind_foreign_project_defect_denied(bind_world):
    db, pid, oid, ver = bind_world["db"], bind_world["p"].id, bind_world["o"].id, bind_world["ver_p"]
    d = _defect(db, pid)
    db.commit()
    snap = _snapshot(db, d.id)
    with pytest.raises(LookupError):
        svc.bind_rd_version(db, oid, d.id, ver.id)
    db.rollback()
    assert _snapshot(db, d.id) == snap
    assert _snapshot(db, d.id)["rd_version_id"] is None


@pytest.mark.parametrize("bad", [True, False, 0, -1, "1", "abc", ""])
def test_bind_invalid_version_id_rejected(bind_world, bad):
    db, pid = bind_world["db"], bind_world["p"].id
    d = _defect(db, pid)
    db.commit()
    snap = _snapshot(db, d.id)
    with pytest.raises(ValueError):
        svc.bind_rd_version(db, pid, d.id, bad)
    db.rollback()
    assert _snapshot(db, d.id) == snap
    assert _snapshot(db, d.id)["rd_version_id"] is None


def test_bind_nonexistent_defect_lookup_error(bind_world):
    db, pid, ver = bind_world["db"], bind_world["p"].id, bind_world["ver_p"]
    existing = _defect(db, pid)
    db.commit()
    snap = _snapshot(db, existing.id)
    with pytest.raises(LookupError):
        svc.bind_rd_version(db, pid, 999999, ver.id)
    db.rollback()
    assert _snapshot(db, existing.id) == snap
    assert _snapshot(db, existing.id)["rd_version_id"] is None


def test_bind_flush_not_commit_caller_rollback_removes_binding(bind_world):
    db, pid, ver = bind_world["db"], bind_world["p"].id, bind_world["ver_p"]
    d = _defect(db, pid)
    db.commit()
    snap = _snapshot(db, d.id)
    out = svc.bind_rd_version(db, pid, d.id, ver.id)
    assert out["rd_version_id"] == ver.id
    # Service must flush, not commit: the binding is visible in-session but the
    # caller still owns the transaction and can roll it back.
    assert db.get(Defect, d.id).rd_version_id == ver.id
    db.rollback()
    assert _snapshot(db, d.id) == snap
    assert _snapshot(db, d.id)["rd_version_id"] is None


# --- DS13E: stale identity-map regression ---
# Two real SQLAlchemy Sessions share one synthetic SQLite engine. Session A
# loads an unbound Defect and keeps the identity object cached; Session B binds
# version 1 and commits. Session A must still see its cached None and must
# reject an attempt to bind a different valid version 2 without overwriting the
# DB binding. A same-version stale retry is allowed to succeed. This proves the
# stale-session behaviour only; it does not claim real PostgreSQL concurrency.


def test_stale_session_cannot_overwrite_binding(bind_world):
    engine = bind_world["db"].get_bind()
    pid, vol_p, ver1 = bind_world["p"].id, bind_world["vol_p"], bind_world["ver_p"]
    with Session(engine) as setup:
        d = _defect(setup, pid)
        setup.commit()
        defect_id = d.id
        ver2 = RdVolumeVersion(
            volume_id=vol_p.id, content_sha256="f" * 64, size_bytes=14,
            file_name="vp3.pdf", mime_type="application/pdf",
        )
        setup.add(ver2)
        setup.commit()
        ver2_id = ver2.id

    with Session(engine) as session_a:
        cached = session_a.get(Defect, defect_id)
        assert cached.rd_version_id is None

        with Session(engine) as session_b:
            out = svc.bind_rd_version(session_b, pid, defect_id, ver1.id)
            assert out["rd_version_id"] == ver1.id
            session_b.commit()

        # Session A still holds the stale identity object with None.
        assert session_a.get(Defect, defect_id) is cached
        assert cached.rd_version_id is None

        # Attempting to bind a different valid version must be rejected and
        # must not overwrite the committed DB binding.
        with pytest.raises(ValueError):
            svc.bind_rd_version(session_a, pid, defect_id, ver2_id)
        session_a.rollback()

        # Same-version stale retry is allowed to succeed.
        retry = svc.bind_rd_version(session_a, pid, defect_id, ver1.id)
        assert retry["rd_version_id"] == ver1.id
        session_a.commit()

    with Session(engine) as fresh:
        final = fresh.get(Defect, defect_id)
        assert final.rd_version_id == ver1.id
