import hashlib
import multiprocessing
import os
import stat
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.integrations.contracts import (
    StorageAccessDenied,
    StorageQuotaExceeded,
    StorageUnavailable,
)
from app.integrations.pu_storage import PuStorageAdapter, RdVersionContentStore
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


# ---------------------------------------------------------------------------
# DS-11I stage6: RdVersionContentStore red tests (tests only).
# These are expected to be RED against the current implementation; they are
# not skipped/xfailed. Parent-dir TOCTOU and global (shared) quota remain
# unresolved separate blockers and are NOT claimed fixed here.
# ---------------------------------------------------------------------------


def _store(tmp_path, quota=1 << 20):
    return RdVersionContentStore(root=tmp_path, quota_bytes=quota)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _blob(tmp_path, pid, digest):
    return tmp_path / ".rd-version-content" / str(pid) / digest


def test_rd_content_put_read_digest(tmp_path):
    s = _store(tmp_path)
    data = b"payload-bytes"
    d = s.put(7, data)
    assert d == _digest(data)
    assert s.read(7, d, 1024) == data
    assert _blob(tmp_path, 7, d).is_file()


def test_rd_content_dedup_same_digest_no_overwrite(tmp_path):
    s = _store(tmp_path)
    data = b"same-content"
    d1 = s.put(7, data)
    before = _blob(tmp_path, 7, d1).stat()
    d2 = s.put(7, data)
    after = _blob(tmp_path, 7, d1).stat()
    assert d1 == d2
    assert before.st_ino == after.st_ino and before.st_mtime_ns == after.st_mtime_ns
    assert len([p for p in (tmp_path / ".rd-version-content" / "7").iterdir() if not p.name.startswith(".")]) == 1


def test_rd_content_changed_bytes_separate(tmp_path):
    s = _store(tmp_path)
    d1 = s.put(7, b"one")
    d2 = s.put(7, b"two")
    assert d1 != d2
    assert s.read(7, d1, 1024) == b"one"
    assert s.read(7, d2, 1024) == b"two"


def test_rd_content_corrupt_existing_rejects_put_and_read(tmp_path):
    s = _store(tmp_path)
    data = b"good"
    d = s.put(7, data)
    blob = _blob(tmp_path, 7, d)
    blob.write_bytes(b"bad")
    with pytest.raises((StorageUnavailable, StorageAccessDenied)):
        s.put(7, data)
    with pytest.raises((StorageUnavailable, StorageAccessDenied)):
        s.read(7, d, 1024)
    assert blob.read_bytes() == b"bad"  # not repaired/overwritten


def test_rd_content_quota_bounded_includes_mutable_root(tmp_path):
    s = _store(tmp_path, quota=10)
    mutable = tmp_path / "7"
    mutable.mkdir()
    (mutable / "existing.bin").write_bytes(b"123456")
    with pytest.raises(StorageQuotaExceeded):
        s.put(7, b"123456")
    assert not _blob(tmp_path, 7, _digest(b"123456")).exists()


def test_rd_content_symlink_project_alias_rejected(tmp_path):
    s = _store(tmp_path)
    base = tmp_path / ".rd-version-content"
    base.mkdir()
    (base / "1").mkdir()
    (base / "2").symlink_to(base / "1")
    with pytest.raises(StorageAccessDenied):
        s.put(2, b"x")
    with pytest.raises(StorageAccessDenied):
        s.read(2, _digest(b"x"), 1024)


def test_rd_content_digest_symlink_outside_rejected(tmp_path):
    s = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_bytes(b"secret")
    data = b"secret"
    d = _digest(data)
    base = tmp_path / ".rd-version-content"
    (base / "7").mkdir(parents=True)
    (base / "7" / d).symlink_to(secret)
    with pytest.raises(StorageAccessDenied):
        s.read(7, d, 1024)
    with pytest.raises(StorageAccessDenied):
        s.put(7, data)
    assert secret.read_bytes() == b"secret"


def test_rd_content_missing_project_read_no_dirs(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(FileNotFoundError):
        s.read(9, _digest(b"x"), 1024)
    assert not (tmp_path / ".rd-version-content" / "9").exists()


def test_rd_content_missing_flags_fail_unavailable(tmp_path, monkeypatch):
    s = _store(tmp_path)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(StorageUnavailable):
        s.put(7, b"x")
    monkeypatch.undo()
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    with pytest.raises(StorageUnavailable):
        s.put(7, b"x")


def _fifo_worker(root, pid, digest, q):
    try:
        s = RdVersionContentStore(root=root, quota_bytes=1 << 20)
        try:
            s.read(pid, digest, 1024)
            q.put("read-ok")
        except StorageAccessDenied:
            q.put("denied")
        except BaseException as exc:  # noqa: BLE001
            q.put(f"other:{type(exc).__name__}")
    except BaseException as exc:  # noqa: BLE001
        q.put(f"init:{type(exc).__name__}")


def test_rd_content_fifo_reaches_kernel_no_stub(tmp_path):
    s = _store(tmp_path)
    data = b"fifo"
    d = _digest(data)
    base = tmp_path / ".rd-version-content"
    (base / "7").mkdir(parents=True)
    os.mkfifo(base / "7" / d)
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    proc = ctx.Process(target=_fifo_worker, args=(tmp_path, 7, d, q))
    proc.start()
    proc.join(2.0)
    if proc.is_alive():
        proc.terminate()
        proc.join(2.0)
        pytest.fail("FIFO read blocked in kernel; O_NONBLOCK guard missing")
    assert proc.exitcode == 0
    assert q.get(timeout=1.0) == "denied"


def _cas_worker(root, pid, payloads, q):
    try:
        s = RdVersionContentStore(root=root, quota_bytes=64)
        out = []
        for p in payloads:
            try:
                out.append(s.put(pid, p))
            except StorageQuotaExceeded:
                out.append("quota")
        q.put(out)
    except BaseException as exc:  # noqa: BLE001
        q.put(f"err:{type(exc).__name__}")


def test_rd_content_concurrent_cas_small_quota(tmp_path):
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    same = b"a" * 16
    procs = [ctx.Process(target=_cas_worker, args=(tmp_path, 7, [same, same], q)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(5.0)
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(2.0)
            pytest.fail("CAS worker hung")
    results = [q.get(timeout=1.0) for _ in procs]
    assert all(isinstance(r, list) for r in results), results
    project_dir = tmp_path / ".rd-version-content" / "7"
    blobs = [p for p in project_dir.iterdir() if not p.name.startswith(".")]
    assert [p.name for p in blobs] == [_digest(same)]
    assert not [p for p in project_dir.iterdir() if p.name.startswith(".tmp-")]
    total = sum(p.stat().st_size for p in blobs)
    assert total <= 64


# ---------------------------------------------------------------------------
# DS-11J: distinct-content race at quota, atomic publication hook, write
# failure injection, and read max_bytes rejection. Tests only; no provider,
# API, TestClient, network or production paths. Parent-directory TOCTOU and
# shared mutable/CAS quota remain unresolved and are NOT claimed covered.
# ---------------------------------------------------------------------------


def _race_worker(root, pid, payload, start_evt, q):
    try:
        s = RdVersionContentStore(root=root, quota_bytes=48)
        start_evt.wait(5.0)
        try:
            q.put(("ok", s.put(pid, payload)))
        except StorageQuotaExceeded:
            q.put(("quota", None))
        except BaseException as exc:  # noqa: BLE001
            q.put((f"err:{type(exc).__name__}", None))
    except BaseException as exc:  # noqa: BLE001
        q.put((f"init:{type(exc).__name__}", None))


def test_rd_content_distinct_race_at_quota(tmp_path):
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    start_evt = ctx.Event()
    payloads = [bytes([65 + i]) * 32 for i in range(3)]
    procs = [ctx.Process(target=_race_worker, args=(tmp_path, 7, p, start_evt, q)) for p in payloads]
    for p in procs:
        p.start()
    start_evt.set()
    for p in procs:
        p.join(5.0)
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(2.0)
            pytest.fail("distinct-content race worker hung")
    assert [p.exitcode for p in procs] == [0, 0, 0]
    results = [q.get(timeout=1.0) for _ in procs]
    kinds = [r[0] for r in results]
    assert kinds.count("ok") == 1, results
    assert kinds.count("quota") == 2, results
    winner_digest = next(r[1] for r in results if r[0] == "ok")
    project_dir = tmp_path / ".rd-version-content" / "7"
    blobs = [p for p in project_dir.iterdir() if not p.name.startswith(".")]
    assert [p.name for p in blobs] == [winner_digest]
    assert not [p for p in project_dir.iterdir() if p.name.startswith(".tmp-")]
    data = blobs[0].read_bytes()
    assert len(data) == 32 and hashlib.sha256(data).hexdigest() == winner_digest
    assert sum(p.stat().st_size for p in blobs) <= 48


def test_rd_content_atomic_publication_hook(tmp_path, monkeypatch):
    s = _store(tmp_path)
    data = b"atomic-publish-payload"
    d = _digest(data)
    target = _blob(tmp_path, 7, d)
    real_link = os.link
    observed = {}

    def hook(src, dst, *a, **kw):
        observed["src"] = src
        observed["dst"] = dst
        observed["target_absent"] = not os.path.exists(target)
        observed["tmp_bytes"] = open(src, "rb").read()
        return real_link(src, dst, *a, **kw)

    monkeypatch.setattr(os, "link", hook)
    assert s.put(7, data) == d
    assert observed["target_absent"] is True
    assert observed["tmp_bytes"] == data
    assert os.path.exists(target)
    assert s.read(7, d, 1024) == data
    assert hashlib.sha256(target.read_bytes()).hexdigest() == d
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".tmp-")]


def test_rd_content_write_failure_no_publish_then_retry(tmp_path, monkeypatch):
    s = _store(tmp_path)
    data = b"write-failure-payload"
    d = _digest(data)
    target = _blob(tmp_path, 7, d)
    real_write = os.write

    def boom(fd, buf):
        raise OSError(5, "injected write failure")

    monkeypatch.setattr(os, "write", boom)
    with pytest.raises(StorageUnavailable):
        s.put(7, data)
    monkeypatch.setattr(os, "write", real_write)
    assert not target.exists()
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
    assert s.put(7, data) == d
    assert s.read(7, d, 1024) == data


def test_rd_content_fsync_failure_no_publish_then_retry(tmp_path, monkeypatch):
    s = _store(tmp_path)
    data = b"fsync-failure-payload"
    d = _digest(data)
    target = _blob(tmp_path, 7, d)
    real_fsync = os.fsync

    def boom(fd):
        raise OSError(5, "injected fsync failure")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(StorageUnavailable):
        s.put(7, data)
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert not target.exists()
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
    assert s.put(7, data) == d
    assert s.read(7, d, 1024) == data


def test_rd_content_read_max_bytes_rejects_oversized(tmp_path):
    s = _store(tmp_path)
    data = b"0123456789"
    d = s.put(7, data)
    with pytest.raises(StorageQuotaExceeded):
        s.read(7, d, 5)
    assert s.read(7, d, len(data)) == data
    assert s.read(7, d, len(data) + 100) == data


# ---------------------------------------------------------------------------
# DS-11K: parent-directory TOCTOU regression (tests only).
#
# The current RdVersionContentStore.read() flow is:
#   1. _project_dir() does lstat() on the project directory (symlink check).
#   2. _open_regular_nofollow() calls os.open(<absolute path>, O_NOFOLLOW).
# O_NOFOLLOW only protects the *final* path component. Between the lstat in
# step 1 and the os.open in step 2 the project directory pathname can be
# renamed aside and replaced with a symlink to another project's directory.
# The final O_NOFOLLOW does not protect the parent component, so the open
# follows the swapped parent and reads the other project's blob.
#
# These tests are expected to be RED against the current implementation.
# They are not skipped/xfailed and the security assertion is not weakened.
# All fixtures are synthetic under tmp_path; no threads, no sleeping, no
# realpath tricks, no network, no API. The os.open hook only fires for the
# actual digest open (after the directory checks) and always delegates to the
# real os.open with the original args/kwargs, so a future dir_fd-anchored
# implementation still runs the real open and the race is genuinely triggered.
# ---------------------------------------------------------------------------


def _swap_project_dir_hook(monkeypatch, base, pid, digest, other_pid):
    """Hook os.open so that, on the digest open, project ``pid`` is swapped.

    The hook is narrow: it only acts when the opened path names the digest
    blob for ``pid`` (i.e. after the initial directory checks have run). It
    renames ``base/pid`` aside and replaces it with a symlink to
    ``base/other_pid``, then calls the real ``os.open`` with the original
    args/kwargs. Returns a dict recording whether the swap fired.
    """
    real_open = os.open
    project_dir = base / str(pid)
    aside = base / f"{pid}.aside"
    state = {"fired": False}

    def hook(path, *args, **kwargs):
        try:
            name = os.fspath(path)
        except TypeError:
            name = None
        if not state["fired"] and isinstance(name, (str, bytes)):
            text = name.decode() if isinstance(name, bytes) else name
            if text.endswith(os.sep + digest) or text == digest:
                # Only swap when the path actually targets this project's
                # digest blob, i.e. after the directory checks have passed.
                if str(project_dir) in text or text == digest:
                    state["fired"] = True
                    try:
                        os.rename(project_dir, aside)
                    except OSError:
                        pass
                    try:
                        os.symlink(base / str(other_pid), project_dir)
                    except OSError:
                        pass
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", hook)
    return state


def test_rd_content_parent_dir_swap_never_reads_other_project(tmp_path, monkeypatch):
    s = _store(tmp_path)
    base = tmp_path / ".rd-version-content"
    base.mkdir()
    project1 = base / "1"
    project2 = base / "2"
    project1.mkdir()
    project2.mkdir()

    # Seed the blob ONLY in project2. project1 stays empty, so the only way
    # read(1, digest) can return the payload is by following the swapped
    # parent symlink into project2.
    data = b"project2-private-bytes"
    d = _digest(data)
    (project2 / d).write_bytes(data)
    assert not (project1 / d).exists()

    state = _swap_project_dir_hook(monkeypatch, base, 1, d, 2)

    try:
        result = s.read(1, d, 1024)
    except (StorageAccessDenied, StorageUnavailable, FileNotFoundError):
        result = None

    # The race must actually have been triggered by the hook.
    assert state["fired"] is True

    # Security assertion: read(1, d) must never return project2's bytes.
    # Either it rejects (AccessDenied/Unavailable/FileNotFound) or, for a
    # future dir_fd-anchored implementation, it reads the original retained
    # project1 (which is empty -> FileNotFoundError). It must never surface
    # the bytes that live only in project2.
    assert result != data
    assert result is None

    # project2's bytes are untouched.
    assert (project2 / d).read_bytes() == data


def test_rd_content_parent_dir_swap_denied_or_notfound(tmp_path, monkeypatch):
    s = _store(tmp_path)
    base = tmp_path / ".rd-version-content"
    base.mkdir()
    project1 = base / "1"
    project2 = base / "2"
    project1.mkdir()
    project2.mkdir()

    data = b"only-in-project2"
    d = _digest(data)
    (project2 / d).write_bytes(data)

    state = _swap_project_dir_hook(monkeypatch, base, 1, d, 2)

    with pytest.raises((StorageAccessDenied, StorageUnavailable, FileNotFoundError)):
        s.read(1, d, 1024)

    assert state["fired"] is True
    assert (project2 / d).read_bytes() == data


def test_rd_content_private_base_swap_never_reads_other_project(tmp_path, monkeypatch):
    """Analogous case: the private base itself is swapped for a symlink.

    The base ``.rd-version-content`` is renamed aside and replaced with a
    symlink to a sibling directory that holds a different project's blob.
    The digest open must not follow the swapped base into the sibling.
    """
    s = _store(tmp_path)
    base = tmp_path / ".rd-version-content"
    base.mkdir()
    project1 = base / "1"
    project1.mkdir()

    # Sibling directory outside the private base holding the payload.
    sibling = tmp_path / "sibling-base"
    sibling.mkdir()
    (sibling / "1").mkdir()
    data = b"sibling-private-bytes"
    d = _digest(data)
    (sibling / "1" / d).write_bytes(data)

    real_open = os.open
    aside = tmp_path / ".rd-version-content.aside"
    state = {"fired": False}

    def hook(path, *args, **kwargs):
        try:
            name = os.fspath(path)
        except TypeError:
            name = None
        if not state["fired"] and isinstance(name, (str, bytes)):
            text = name.decode() if isinstance(name, bytes) else name
            if text.endswith(os.sep + d) or text == d:
                state["fired"] = True
                try:
                    os.rename(base, aside)
                except OSError:
                    pass
                try:
                    os.symlink(sibling, base)
                except OSError:
                    pass
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", hook)

    try:
        result = s.read(1, d, 1024)
    except (StorageAccessDenied, StorageUnavailable, FileNotFoundError):
        result = None

    assert state["fired"] is True
    assert result != data
    assert result is None
    assert (sibling / "1" / d).read_bytes() == data
