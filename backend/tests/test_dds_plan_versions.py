from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.dds_plan_version import DdsPlanVersion
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.api import dds_plan_versions as api


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="DDS org")
        user = User(name="Manager", email="dds-manager@example.test", is_admin=False)
        viewer = User(name="Viewer", email="dds-viewer@example.test", is_admin=False)
        db.add_all([org, user, viewer])
        db.flush()
        project = Project(name="DDS project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        db.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="viewer"))
        db.commit()
        yield {
            "db": db,
            "project": project,
            "user": user,
            "viewer": viewer,
        }
    engine.dispose()


def _add_entry(db, project_id, **kwargs):
    values = {
        "direction": "outflow",
        "title": "Op",
        "planned_date": date(2026, 1, 15),
        "actual_date": None,
        "planned_amount": Decimal("0.00"),
        "actual_amount": Decimal("0.00"),
        "currency": "RUB",
        "status": "approved",
    }
    values.update(kwargs)
    entry = CashFlowEntry(project_id=project_id, **values)
    db.add(entry)
    db.flush()
    return entry


def test_create_versions_numbering(world):
    db = world["db"]
    project = world["project"]
    user = world["user"]

    _add_entry(
        db,
        project.id,
        title="Материалы",
        planned_date=date(2026, 1, 10),
        planned_amount=Decimal("1000.00"),
        direction="outflow",
    )
    db.commit()

    first = api.create_dds_plan_version(
        project_id=project.id,
        payload=api.DdsPlanVersionCreate(label="Пересмотр 1", effective_from=date(2026, 1, 1)),
        db=db,
        user=user,
    )
    # ensure_initial_version creates version 1, user's becomes version 2
    assert first["version_number"] == 2

    initial = db.query(DdsPlanVersion).filter_by(project_id=project.id).order_by(DdsPlanVersion.version_number).first()
    assert initial is not None
    assert initial.version_number == 1
    assert initial.label == "Исходный план"

    second = api.create_dds_plan_version(
        project_id=project.id,
        payload=api.DdsPlanVersionCreate(label="Пересмотр 2", effective_from=None),
        db=db,
        user=user,
    )
    assert second["version_number"] == 3


def test_compare_base_version_with_current(world):
    db = world["db"]
    project = world["project"]
    user = world["user"]

    entry = _add_entry(
        db,
        project.id,
        title="Материалы",
        planned_date=date(2026, 1, 10),
        planned_amount=Decimal("1000.00"),
        direction="outflow",
    )
    db.commit()

    created = api.create_dds_plan_version(
        project_id=project.id,
        payload=api.DdsPlanVersionCreate(label="Пересмотр 1", effective_from=None),
        db=db,
        user=user,
    )
    base_id = created["id"]

    entry.planned_amount = Decimal("1500.00")
    db.commit()

    result = api.compare_dds_plan_versions(
        project_id=project.id,
        base=base_id,
        target="current",
        db=db,
        user=user,
    )
    assert result["totals"]["outflow"]["base"] == "1000.00"
    assert result["totals"]["outflow"]["target"] == "1500.00"
    assert result["totals"]["outflow"]["difference"] == "500.00"
    target_article = next(a for a in result["articles"] if a["title"] == "Материалы")
    assert target_article["difference"] == "500.00"
    assert target_article["months"][0]["month"] == "2026-01"
    assert target_article["months"][0]["difference"] == "500.00"


def test_compare_added_removed(world):
    db = world["db"]
    project = world["project"]
    user = world["user"]

    _add_entry(
        db,
        project.id,
        title="Материалы",
        planned_date=date(2026, 1, 10),
        planned_amount=Decimal("500.00"),
        direction="outflow",
    )
    db.commit()

    created = api.create_dds_plan_version(
        project_id=project.id,
        payload=api.DdsPlanVersionCreate(label="Пересмотр 1", effective_from=None),
        db=db,
        user=user,
    )
    base_id = created["id"]

    _add_entry(
        db,
        project.id,
        title="Логистика",
        planned_date=date(2026, 2, 5),
        planned_amount=Decimal("300.00"),
        direction="outflow",
    )
    db.commit()

    result = api.compare_dds_plan_versions(
        project_id=project.id,
        base=base_id,
        target="current",
        db=db,
        user=user,
    )
    assert "Логистика" in result["added"]

    # Remove Материалы then compare -> should be "removed"
    db.query(CashFlowEntry).filter_by(project_id=project.id).filter(
        CashFlowEntry.title == "Материалы"
    ).delete()
    db.commit()

    result2 = api.compare_dds_plan_versions(
        project_id=project.id,
        base=base_id,
        target="current",
        db=db,
        user=user,
    )
    assert "Материалы" in result2["removed"]


def test_list_accessible_for_viewer(world):
    db = world["db"]
    project = world["project"]
    user = world["user"]
    viewer = world["viewer"]

    _add_entry(
        db,
        project.id,
        title="Материалы",
        planned_date=date(2026, 1, 10),
        planned_amount=Decimal("100.00"),
        direction="outflow",
    )
    db.commit()

    api.create_dds_plan_version(
        project_id=project.id,
        payload=api.DdsPlanVersionCreate(label="Пересмотр 1", effective_from=None),
        db=db,
        user=user,
    )

    items = api.list_dds_plan_versions(
        project_id=project.id,
        contract_id=None,
        db=db,
        user=viewer,
    )
    assert len(items) >= 1
    assert items[0]["version_number"] == 1
