import os

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.integrations.contracts import StorageAccessDenied, StorageQuotaExceeded
from app.integrations.pu_storage import PuStorageAdapter
from app.integrations.storage import validate_storage_locator
from app.models.drive_connection import DriveConnection
from app.models.organization_contract import Organization
from app.models.project import Project


def test_create_write_list_read(tmp_path):
    a = PuStorageAdapter(1, root=tmp_path)
    folder = a.create_folder("Договоры", "pu:/")
    assert folder == "pu:/Договоры"
    assert a.create_folder("Договоры", "pu:/") == folder
    fid = a.write_file(folder, "a.txt", b"hello")
    kids = a.list_children("pu:/")
    assert [k.name for k in kids] == ["Договоры"] and kids[0].is_folder
    assert a.read_bytes(fid, 100)[0] == b"hello"
    assert [k.name for k in a.list_children(folder)] == ["a.txt"]


def test_duplicate_name_gets_suffix(tmp_path):
    a = PuStorageAdapter(1, root=tmp_path)
    first = a.write_file("pu:/", "a.txt", b"1")
    second = a.write_file("pu:/", "a.txt", b"2")
    assert first == "pu:/a.txt" and second == "pu:/a (2).txt"


def test_path_escapes_are_denied(tmp_path):
    a = PuStorageAdapter(1, root=tmp_path)
    for bad in ("pu:/../x", "/etc/passwd", "disk:/a", "pu:/a/../../b"):
        with pytest.raises(StorageAccessDenied):
            a.list_children(bad)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("secret")
    a._ensure_project_root()
    (tmp_path / "1" / "link").symlink_to(outside)
    with pytest.raises(StorageAccessDenied):
        a.read_bytes("pu:/link/s.txt", 100)


def test_quota(tmp_path):
    a = PuStorageAdapter(1, root=tmp_path, quota_bytes=10)
    a.write_file("pu:/", "a.bin", b"123456")
    with pytest.raises(StorageQuotaExceeded):
        a.write_file("pu:/", "b.bin", b"123456")


def test_mutations_only_inside_copy(tmp_path):
    a = PuStorageAdapter(1, root=tmp_path)
    copy = a.create_folder("copy", "pu:/")
    other = a.create_folder("other", "pu:/")
    f = a.write_file(copy, "x.txt", b"x")
    g = a.write_file(other, "y.txt", b"y")
    a.rename_file(f, "z.txt", copy)
    assert (tmp_path / "1" / "copy" / "z.txt").exists()
    with pytest.raises(StorageAccessDenied):
        a.rename_file(g, "q.txt", copy)
    sub = a.create_folder("sub", copy)
    a.move_file("pu:/copy/z.txt", sub, copy, copy)
    assert (tmp_path / "1" / "copy" / "sub" / "z.txt").exists()
    with pytest.raises(StorageAccessDenied):
        a.move_file("pu:/copy/sub/z.txt", other, sub, copy)
    a.trash_safe_copy(copy)
    assert not (tmp_path / "1" / "copy").exists()
    assert list((tmp_path / "1" / ".trash").iterdir())
    assert [k.name for k in a.list_children("pu:/")] == ["other"]


def test_copy_folder_tree(tmp_path):
    a = PuStorageAdapter(1, root=tmp_path)
    src = a.create_folder("src", "pu:/")
    a.write_file(src, "a.txt", b"a")
    sub = a.create_folder("sub", src)
    a.write_file(sub, "b.txt", b"b")
    dest = a.create_folder("dest", "pu:/")
    res = a.copy_folder_tree(src, dest, "src", idempotency_key="k1")
    assert res.item_count == 3
    assert res.id_map[src] == res.copy_root_id
    assert (tmp_path / "1" / "dest" / res.copy_root_name / "sub" / "b.txt").exists()


def test_validate_locator():
    assert validate_storage_locator("pu_storage", "pu:/a") == "pu:/a"
    for bad in ("pu:/../a", "disk:/"):
        with pytest.raises(HTTPException):
            validate_storage_locator("pu_storage", bad)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PU_STORAGE_ROOT", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="PU org")
        db.add(org)
        db.flush()
        p = Project(name="P", organization_id=org.id)
        o = Project(name="O", organization_id=org.id)
        db.add_all([p, o])
        db.flush()
        db.commit()
        yield {"db": db, "p": p, "o": o}
    engine.dispose()


def _client(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import pu_storage as api
    from app.core.auth import require_user
    from app.database import get_db

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: world["db"]
    app.dependency_overrides[require_user] = lambda: type("U", (), {"id": 1, "is_active": True, "is_admin": True})()
    return TestClient(app), api


def test_api_connect_upload_download(world):
    client, _api = _client(world)
    pid = world["p"].id
    r = client.post(f"/projects/{pid}/pu-storage/connect")
    assert r.status_code == 200 and r.json()["provider"] == "pu_storage"
    assert client.post(f"/projects/{pid}/pu-storage/connect").status_code == 200
    assert world["db"].query(DriveConnection).filter_by(project_id=pid).count() == 1
    r = client.post(f"/projects/{pid}/pu-storage/folders", json={"parent_id": "pu:/", "name": "Док"})
    assert r.status_code == 200
    r = client.post(
        f"/projects/{pid}/pu-storage/upload",
        params={"parent_id": "pu:/Док", "name": "a.txt"},
        content=b"hello",
    )
    assert r.status_code == 200 and r.json()["uploaded"][0]["size"] == 5
    r = client.get(f"/projects/{pid}/pu-storage/files", params={"folder_id": "pu:/Док"})
    assert r.status_code == 200 and r.json()["files"][0]["name"] == "a.txt"
    assert r.json()["usage"]["used_bytes"] == 5
    r = client.get(f"/projects/{pid}/pu-storage/download", params={"object_id": "pu:/Док/a.txt"})
    assert r.status_code == 200 and r.content == b"hello"
    r = client.get(f"/projects/{pid}/pu-storage/download", params={"object_id": "pu:/Док"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert client.get(f"/projects/{pid}/pu-storage/download", params={"object_id": "pu:/../x"}).status_code == 403


def test_api_conflicting_provider_and_quota(world, monkeypatch):
    client, _api = _client(world)
    pid = world["p"].id
    world["db"].add(DriveConnection(project_id=pid, provider="google_drive", account_email="a@b.c", root_folder_id="x", status="connected"))
    world["db"].commit()
    assert client.post(f"/projects/{pid}/pu-storage/connect").status_code == 409
    other = world["o"].id
    monkeypatch.setenv("PU_STORAGE_QUOTA_MB", "1")
    big = b"x" * (2 * 1024 * 1024)
    r = client.post(f"/projects/{other}/pu-storage/upload", params={"parent_id": "pu:/", "name": "big.bin"}, content=big)
    assert r.status_code == 413
