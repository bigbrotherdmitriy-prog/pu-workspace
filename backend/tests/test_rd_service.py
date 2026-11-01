from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app import rd_service
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdSpecItem, RdVolume, RdVolumeVersion


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="RD org")
        db.add(org)
        db.flush()
        project = Project(name="RD project", organization_id=org.id)
        other = Project(name="Other", organization_id=org.id)
        db.add_all([project, other])
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "other": other}
    engine.dispose()


class _FakeExec:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return self._data


class _FakeFiles:
    def __init__(self, files):
        self._files = files

    def list(self, **_kwargs):
        return _FakeExec({"files": self._files})

    def get_media(self, **_kwargs):
        return None


class _FakeDrive:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def _patch_drive(monkeypatch, files):
    fake = _FakeDrive(_FakeFiles(files))
    monkeypatch.setattr("app.api.google_drive.drive_service_for_project", lambda *a, **k: fake)
    return fake


def test_guess_meta_basic():
    meta = rd_service.guess_meta("Объект-ЭМ.pdf")
    assert meta["section"] == "ЭМ"
    assert meta["code"] == "Объект"
    assert meta["object_name"] is None

    meta2 = rd_service.guess_meta("Проект.pdf")
    assert meta2["section"] is None
    assert meta2["code"] == "Проект"

    meta3 = rd_service.guess_meta("abc-VOK.pdf")
    assert meta3["section"] == "VOK"
    assert meta3["code"] == "abc"


def test_import_folder_creates_and_dedups(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    files = [
        {"id": "f1", "name": "A-ЭМ.pdf", "size": "100", "mimeType": "application/pdf"},
        {"id": "f2", "name": "B-ЭМ.pdf", "size": "200", "mimeType": "application/pdf"},
    ]
    _patch_drive(monkeypatch, files)

    res = rd_service.import_folder(db, project.id, "folder-1")
    assert res["added"] == 2
    assert res["existing"] == 0

    vols = db.execute(select(RdVolume).where(RdVolume.project_id == project.id)).scalars().all()
    assert len(vols) == 2

    res2 = rd_service.import_folder(db, project.id, "folder-1")
    assert res2["added"] == 0
    assert res2["existing"] == 2


def _make_volume(db, project_id, *, external_id="drive-1", status="new"):
    vol = RdVolume(
        project_id=project_id,
        title="Том",
        file_name="t.pdf",
        provider="google_drive",
        external_id=external_id,
        status=status,
    )
    db.add(vol)
    db.flush()
    db.commit()
    return vol


def test_parse_volume_creates_items_and_keeps_confirmed(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id)

    confirmed = RdSpecItem(
        volume_id=vol.id, project_id=project.id, sort_order=0,
        name="Старая подтверждённая", status="confirmed",
    )
    proposed_old = RdSpecItem(
        volume_id=vol.id, project_id=project.id, sort_order=0,
        name="Stale proposed", status="proposed",
    )
    db.add_all([confirmed, proposed_old])
    db.commit()

    _patch_drive(monkeypatch, [])
    monkeypatch.setattr(rd_service, "_download", lambda svc, fid: b"pdf-bytes")
    monkeypatch.setattr(rd_service, "split_pages", lambda b: ["p1", "p2"])
    monkeypatch.setattr(rd_service, "find_spec_pages", lambda pages: [1])
    monkeypatch.setattr(rd_service, "spec_text", lambda pages, nums: "text")

    def fake_spec(text):
        return ([
            {"pos": "1", "name": "Кабель", "type": "ВВГ", "unit": "м", "qty": "10,5", "note": None},
            {"pos": "2", "name": "Щит", "qty": None},
            {"pos": "3", "type": "X"},
        ], "llm")

    monkeypatch.setattr(rd_service, "specification_from_text", fake_spec)

    res = rd_service.parse_volume(db, vol.id)
    assert res["items"] == 2
    assert res["spec_pages"] == "1"

    db.expire_all()
    vol_db = db.get(RdVolume, vol.id)
    assert vol_db is not None
    assert vol_db.status == "parsed"
    assert vol_db.page_count == 2

    items = db.execute(
        select(RdSpecItem).where(RdSpecItem.volume_id == vol.id)
    ).scalars().all()
    statuses = sorted(i.status for i in items)
    assert statuses == ["confirmed", "proposed", "proposed"]
    proposed = [i for i in items if i.status == "proposed"]
    assert all(i.source_page == 1 for i in proposed)
    assert any(i.qty == Decimal("10.5") for i in proposed)


def test_parse_volume_page_error_does_not_fail(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-page-err")

    _patch_drive(monkeypatch, [])
    monkeypatch.setattr(rd_service, "_download", lambda svc, fid: b"pdf-bytes")
    monkeypatch.setattr(rd_service, "split_pages", lambda b: ["p1", "p2", "p3"])
    monkeypatch.setattr(rd_service, "find_spec_pages", lambda pages: [1, 2])
    monkeypatch.setattr(rd_service, "spec_text", lambda pages, nums: f"text{nums[0]}")

    def fake_spec(text):
        if "text2" in text:
            raise RuntimeError("boom")
        return ([{"name": "Позиция", "qty": 1}], "llm")

    monkeypatch.setattr(rd_service, "specification_from_text", fake_spec)

    res = rd_service.parse_volume(db, vol.id)
    assert res["items"] == 1

    db.expire_all()
    vol_db = db.get(RdVolume, vol.id)
    assert vol_db is not None
    assert vol_db.status == "parsed"
    assert vol_db.error_message and "стр.2" in vol_db.error_message


def test_parse_volume_download_error_sets_failed(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-dl-err")

    _patch_drive(monkeypatch, [])

    def boom(svc, fid):
        raise RuntimeError("download failed")

    monkeypatch.setattr(rd_service, "_download", boom)

    res = rd_service.parse_volume(db, vol.id)
    assert res["items"] == 0

    db.expire_all()
    vol_db = db.get(RdVolume, vol.id)
    assert vol_db is not None
    assert vol_db.status == "failed"
    assert vol_db.error_message and "download failed" in vol_db.error_message


def test_set_item_status_and_update_item(world):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-status")
    item = RdSpecItem(
        volume_id=vol.id, project_id=project.id, sort_order=1,
        name="Исходное", qty=Decimal("1"), status="proposed",
    )
    db.add(item)
    db.commit()

    updated = rd_service.set_item_status(db, project.id, item.id, "confirmed")
    assert updated.status == "confirmed"

    edited = rd_service.update_item(db, project.id, item.id, {"name": "Новое", "qty": "5.5"})
    assert edited.name == "Новое"
    assert edited.qty == Decimal("5.5")
    assert edited.status == "confirmed"


def test_foreign_project_404(world):
    from fastapi import HTTPException

    db = world["db"]
    project = world["project"]
    other = world["other"]
    vol = _make_volume(db, project.id, external_id="drive-foreign")
    item = RdSpecItem(
        volume_id=vol.id, project_id=project.id, sort_order=1, name="X",
    )
    db.add(item)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        rd_service.set_item_status(db, other.id, item.id, "confirmed")
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info2:
        rd_service.update_item(db, other.id, item.id, {"name": "Y"})
    assert exc_info2.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info3:
        rd_service.set_item_status(db, project.id, item.id, "bogus")
    assert exc_info3.value.status_code == 422


def test_parse_volume_does_not_duplicate_confirmed_and_removes_exact_copies(world, monkeypatch):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id)
    for _ in range(2):  # уже накопившийся дубль подтверждённой позиции
        db.add(RdSpecItem(
            volume_id=vol.id, project_id=project.id, sort_order=1, pos="1", name="Кабель",
            type_mark="ВВГ", unit="м", qty=Decimal("10"), source_page=1, status="confirmed",
        ))
    db.commit()

    _patch_drive(monkeypatch, [])
    monkeypatch.setattr(rd_service, "_download", lambda svc, fid: b"pdf-bytes")
    monkeypatch.setattr(rd_service, "split_pages", lambda b: ["p1"])
    monkeypatch.setattr(rd_service, "find_spec_pages", lambda pages: [1])
    monkeypatch.setattr(rd_service, "spec_text", lambda pages, nums: "text")
    monkeypatch.setattr(rd_service, "specification_from_text", lambda text: ([
        {"pos": "1", "name": "Кабель", "type": "ВВГ", "unit": "м", "qty": "10", "note": None},
        {"pos": "2", "name": "Щит", "type": "ЩР", "unit": "шт", "qty": "1", "note": None},
    ], "llm"))

    res = rd_service.parse_volume(db, vol.id)
    assert res["items"] == 1  # новая только «Щит»
    db.expire_all()
    items = db.execute(select(RdSpecItem).where(RdSpecItem.volume_id == vol.id)).scalars().all()
    assert sorted((i.name, i.status) for i in items) == [("Кабель", "confirmed"), ("Щит", "proposed")]


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("provider/OCR path must not be called by record_volume_version")


def _version_rows(db, volume_id):
    return db.execute(
        select(RdVolumeVersion).where(RdVolumeVersion.volume_id == volume_id)
    ).scalars().all()


def test_record_volume_version_metadata_and_dedup(world, monkeypatch):
    import hashlib

    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-version")

    monkeypatch.setattr(rd_service, "_download", _fail_if_called)
    monkeypatch.setattr(rd_service, "split_pages", _fail_if_called)
    monkeypatch.setattr(rd_service, "find_spec_pages", _fail_if_called)
    monkeypatch.setattr(rd_service, "spec_text", _fail_if_called)
    monkeypatch.setattr(rd_service, "specification_from_text", _fail_if_called)

    content = b"%PDF-1.4 synthetic volume bytes"
    expected_digest = hashlib.sha256(content).hexdigest()

    first = rd_service.record_volume_version(
        db, project.id, vol.id, content, "  plan.pdf  ", "  application/pdf  ",
    )
    assert first.id is not None
    assert first.volume_id == vol.id
    assert first.content_sha256 == expected_digest
    assert first.size_bytes == len(content)
    assert first.file_name == "plan.pdf"
    assert first.mime_type == "application/pdf"
    first_id = first.id
    first_created = first.created_at

    # same bytes, different name/mime -> same row, original fields preserved
    again = rd_service.record_volume_version(
        db, project.id, vol.id, content, "other.pdf", "image/png",
    )
    assert again.id == first_id
    assert again.file_name == "plan.pdf"
    assert again.mime_type == "application/pdf"
    assert again.created_at == first_created
    assert len(_version_rows(db, vol.id)) == 1

    # different bytes -> new row
    other_content = b"%PDF-1.4 different synthetic bytes"
    second = rd_service.record_volume_version(
        db, project.id, vol.id, other_content, "plan2.pdf", "application/pdf",
    )
    assert second.id != first_id
    assert second.content_sha256 == hashlib.sha256(other_content).hexdigest()
    assert second.size_bytes == len(other_content)
    assert len(_version_rows(db, vol.id)) == 2


def test_record_volume_version_accepts_bytes_like(world):
    import hashlib

    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-bytes-like")

    payload = b"bytes-like synthetic payload"
    for variant in (bytearray(payload), memoryview(payload)):
        version = rd_service.record_volume_version(
            db, project.id, vol.id, variant, "v.pdf", "application/pdf",
        )
        assert version.content_sha256 == hashlib.sha256(payload).hexdigest()
        assert version.size_bytes == len(payload)
    assert len(_version_rows(db, vol.id)) == 1


def test_record_volume_version_lookup_errors_are_identical(world):
    db = world["db"]
    project = world["project"]
    other = world["other"]
    vol = _make_volume(db, project.id, external_id="drive-lookup")

    with pytest.raises(LookupError) as wrong_project:
        rd_service.record_volume_version(
            db, other.id, vol.id, b"synthetic", "a.pdf", "application/pdf",
        )
    with pytest.raises(LookupError) as missing_volume:
        rd_service.record_volume_version(
            db, project.id, 999999, b"synthetic", "a.pdf", "application/pdf",
        )
    assert str(wrong_project.value) == str(missing_volume.value)
    assert _version_rows(db, vol.id) == []


def test_record_volume_version_does_not_commit(world):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-no-commit")

    version = rd_service.record_volume_version(
        db, project.id, vol.id, b"synthetic rollback bytes", "r.pdf", "application/pdf",
    )
    version_id = version.id
    assert version_id is not None

    db.rollback()

    assert db.get(RdVolumeVersion, version_id) is None
    assert _version_rows(db, vol.id) == []
    assert db.get(RdVolume, vol.id) is not None


def test_record_volume_version_validation_errors(world):
    db = world["db"]
    project = world["project"]
    vol = _make_volume(db, project.id, external_id="drive-validation")

    with pytest.raises(TypeError):
        rd_service.record_volume_version(
            db, project.id, vol.id, "not-bytes", "a.pdf", "application/pdf",
        )

    for bad_name, bad_mime in (("   ", "application/pdf"), ("a.pdf", "  ")):
        with pytest.raises(ValueError):
            rd_service.record_volume_version(
                db, project.id, vol.id, b"synthetic", bad_name, bad_mime,
            )

    assert _version_rows(db, vol.id) == []
