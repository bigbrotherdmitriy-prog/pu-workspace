import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import executive_docs as api
from app.core.auth import require_user
from app.database import Base, get_db
from app.integrations.pu_storage import PuStorageAdapter
from app.models.executive_docs import IdSource
from app.models.organization_contract import Organization
from app.models.project import Project


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PU_STORAGE_ROOT", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="ID org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        PuStorageAdapter(p.id).create_folder("ИД", "pu:/")
        src = IdSource(project_id=p.id, object_name="ДУБНА", provider="pu_storage", folder_id="pu:/ИД")
        db.add(src)
        db.commit()
        yield {"db": db, "p": p, "o": o, "src": src, "tmp": tmp_path}
    engine.dispose()


def _client(world, monkeypatch):
    from app.integrations import storage as storage_mod

    monkeypatch.setattr(storage_mod, "storage_for_project", lambda pid, db, connection=None: PuStorageAdapter(pid))
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    return TestClient(app)


def test_upload_writes_file_and_rescans(world, monkeypatch):
    client = _client(world, monkeypatch)
    pid, sid = world["p"].id, world["src"].id
    r = client.post(f"/projects/{pid}/executive-docs/sources/{sid}/upload", params={"name": "Приказ.pdf"}, content=b"%PDF-1.4 test")
    assert r.status_code == 200, r.text
    assert r.json()["file_id"] == "pu:/ИД/Приказ.pdf"
    assert (world["tmp"] / str(pid) / "ИД" / "Приказ.pdf").read_bytes() == b"%PDF-1.4 test"
    assert "report" in r.json()
    assert world["db"].query(IdSource).get(sid).last_scan_at is not None


def test_upload_rejects_empty_unknown_source_and_other_project(world, monkeypatch):
    client = _client(world, monkeypatch)
    pid, sid = world["p"].id, world["src"].id
    assert client.post(f"/projects/{pid}/executive-docs/sources/{sid}/upload", params={"name": "a.pdf"}, content=b"").status_code == 422
    assert client.post(f"/projects/{pid}/executive-docs/sources/9999/upload", params={"name": "a.pdf"}, content=b"x").status_code == 404
    assert client.post(f"/projects/{world['o'].id}/executive-docs/sources/{sid}/upload", params={"name": "a.pdf"}, content=b"x").status_code == 404
