from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
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
        db.add(project)
        db.flush()
        db.commit()
        yield {"db": db, "project": project, "org": org}
    engine.dispose()


def _make_volume(db, project_id, *, external_id="drive-file-1"):
    vol = RdVolume(
        project_id=project_id,
        object_name="Объект",
        section="ЭМ",
        code="ЭМ-01",
        title="Электроосвещение",
        file_name="em-01.pdf",
        provider="google_drive",
        external_id=external_id,
    )
    db.add(vol)
    db.flush()
    return vol


def _make_version(db, volume_id, *, digest="a" * 64, size_bytes=1024):
    ver = RdVolumeVersion(
        volume_id=volume_id,
        content_sha256=digest,
        size_bytes=size_bytes,
        file_name="em-01-v1.pdf",
        mime_type="application/pdf",
    )
    db.add(ver)
    db.flush()
    return ver


def test_rd_models_defaults_and_qty(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    assert vol.status == "new"
    assert vol.provider == "google_drive"

    item1 = RdSpecItem(
        volume_id=vol.id,
        project_id=project.id,
        sort_order=1,
        name="Светильник",
        unit="шт",
        qty=Decimal("6.000"),
    )
    item2 = RdSpecItem(
        volume_id=vol.id,
        project_id=project.id,
        sort_order=2,
        name="Кабель",
        qty=None,
    )
    db.add_all([item1, item2])
    db.commit()

    db.expire_all()
    stored = db.get(RdSpecItem, item1.id)
    assert stored is not None
    assert stored.qty == Decimal("6.000")
    assert stored.status == "proposed"

    stored2 = db.get(RdSpecItem, item2.id)
    assert stored2 is not None
    assert stored2.status == "proposed"
    assert stored2.qty is None

    vol_db = db.get(RdVolume, vol.id)
    assert vol_db is not None
    assert vol_db.status == "new"


def test_volume_unique_source_violation(world):
    db = world["db"]
    project = world["project"]

    _make_volume(db, project.id, external_id="dup-file")
    db.commit()

    dup = RdVolume(
        project_id=project.id,
        title="Дубль",
        file_name="dup.pdf",
        provider="google_drive",
        external_id="dup-file",
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_volume_delete_cascades_spec_items(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    db.add_all([
        RdSpecItem(volume_id=vol.id, project_id=project.id, name="Позиция 1"),
        RdSpecItem(volume_id=vol.id, project_id=project.id, name="Позиция 2"),
    ])
    db.commit()

    volume_id = vol.id
    db.delete(vol)
    db.commit()

    remaining = db.execute(
        select(RdSpecItem).where(RdSpecItem.volume_id == volume_id)
    ).scalars().all()
    assert remaining == []

    assert db.get(RdVolume, volume_id) is None


def test_volume_version_valid_insert(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    ver = _make_version(db, vol.id)
    db.commit()

    db.expire_all()
    stored = db.get(RdVolumeVersion, ver.id)
    assert stored is not None
    assert stored.id is not None
    assert stored.created_at is not None
    assert stored.volume_id == vol.id
    assert stored.content_sha256 == "a" * 64
    assert stored.size_bytes == 1024
    assert stored.file_name == "em-01-v1.pdf"
    assert stored.mime_type == "application/pdf"

    vol_db = db.get(RdVolume, vol.id)
    assert vol_db is not None
    assert stored.volume_id == vol_db.id


def test_volume_version_distinct_digests_same_volume(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    v1 = _make_version(db, vol.id, digest="a" * 64)
    v2 = _make_version(db, vol.id, digest="b" * 64)
    db.commit()

    assert v1.id != v2.id
    rows = db.execute(
        select(RdVolumeVersion).where(RdVolumeVersion.volume_id == vol.id)
    ).scalars().all()
    assert len(rows) == 2
    assert {r.content_sha256 for r in rows} == {"a" * 64, "b" * 64}


def test_volume_version_duplicate_digest_same_volume_rejected(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    _make_version(db, vol.id, digest="c" * 64)
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            _make_version(db, vol.id, digest="c" * 64)
    db.rollback()

    rows = db.execute(
        select(RdVolumeVersion).where(RdVolumeVersion.volume_id == vol.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].content_sha256 == "c" * 64


def test_volume_version_same_digest_different_volume_allowed(world):
    db = world["db"]
    project = world["project"]

    vol1 = _make_volume(db, project.id, external_id="file-a")
    vol2 = _make_volume(db, project.id, external_id="file-b")
    v1 = _make_version(db, vol1.id, digest="d" * 64)
    v2 = _make_version(db, vol2.id, digest="d" * 64)
    db.commit()

    assert v1.id != v2.id
    assert v1.volume_id != v2.volume_id


@pytest.mark.parametrize(
    "digest,size_bytes",
    [
        ("a" * 64, -1),
        ("a" * 63, 10),
        ("a" * 65, 10),
    ],
)
def test_volume_version_invalid_digest_or_size_rejected(world, digest, size_bytes):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            _make_version(db, vol.id, digest=digest, size_bytes=size_bytes)
    db.rollback()

    rows = db.execute(
        select(RdVolumeVersion).where(RdVolumeVersion.volume_id == vol.id)
    ).scalars().all()
    assert rows == []


def test_volume_version_required_columns_not_null(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(RdVolumeVersion(
                volume_id=vol.id,
                content_sha256=None,
                size_bytes=10,
                file_name="x.pdf",
                mime_type="application/pdf",
            ))
            db.flush()
    db.rollback()

    rows = db.execute(
        select(RdVolumeVersion).where(RdVolumeVersion.volume_id == vol.id)
    ).scalars().all()
    assert rows == []


def test_volume_delete_with_version_restricted(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    ver = _make_version(db, vol.id)
    db.commit()

    volume_id = vol.id
    version_id = ver.id

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.delete(db.get(RdVolume, volume_id))
            db.flush()
    db.rollback()

    db.expire_all()
    assert db.get(RdVolume, volume_id) is not None
    stored = db.get(RdVolumeVersion, version_id)
    assert stored is not None
    assert stored.volume_id == volume_id


def test_volume_delete_without_versions_works(world):
    db = world["db"]
    project = world["project"]

    vol = _make_volume(db, project.id)
    db.commit()

    volume_id = vol.id
    db.delete(vol)
    db.commit()

    assert db.get(RdVolume, volume_id) is None


def test_volume_version_project_scope_via_volume_join(world):
    db = world["db"]
    project = world["project"]
    org = world["org"]

    other_project = Project(name="Other project", organization_id=org.id)
    db.add(other_project)
    db.flush()

    own_vol = _make_volume(db, project.id, external_id="own-file")
    other_vol = _make_volume(db, other_project.id, external_id="other-file")
    own_ver = _make_version(db, own_vol.id, digest="e" * 64)
    other_ver = _make_version(db, other_vol.id, digest="f" * 64)
    db.commit()

    scoped = db.execute(
        select(RdVolumeVersion)
        .join(RdVolume, RdVolumeVersion.volume_id == RdVolume.id)
        .where(RdVolume.project_id == project.id)
    ).scalars().all()

    ids = {v.id for v in scoped}
    assert own_ver.id in ids
    assert other_ver.id not in ids
    assert all(v.volume_id == own_vol.id for v in scoped)
