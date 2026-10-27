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
from app.models.rd import RdSpecItem, RdVolume


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
