from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register every mapped table
from app.core.auth import require_user
from app.database import Base, get_db
from app.main import app
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


def location_fields(body: dict) -> dict:
    keys = {"configured", "latitude", "longitude", "label", "radius_m", "accuracy_m"}
    return {key: body[key] for key in keys}


@pytest.fixture
def site_location_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)

    organization = Organization(name="Тестовая организация")
    owner = User(name="Владелец", email="owner-location@example.test", is_admin=False)
    other_owner = User(name="Чужой владелец", email="other-location@example.test", is_admin=False)
    session.add_all([organization, owner, other_owner])
    session.flush()

    project = Project(name="Мой объект", organization_id=organization.id)
    other_project = Project(name="Чужой объект", organization_id=organization.id)
    session.add_all([project, other_project])
    session.flush()
    session.add_all([
        ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
        ProjectMember(project_id=other_project.id, user_id=other_owner.id, role="owner"),
    ])
    session.commit()

    current_user = {"value": owner}

    def override_db() -> Iterator[Session]:
        yield session

    def override_user() -> User:
        return current_user["value"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = override_user
    client = TestClient(app)
    try:
        yield client, current_user, owner, other_owner, project, other_project
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_user, None)
        session.close()
        engine.dispose()


def test_site_location_is_unconfigured_until_owner_explicitly_sets_it(site_location_client):
    client, _, _, _, project, _ = site_location_client

    initial = client.get(f"/projects/{project.id}/site-location")
    assert initial.status_code == 200
    assert location_fields(initial.json()) == {
        "configured": False,
        "latitude": None,
        "longitude": None,
        "label": None,
        "radius_m": 250,
        "accuracy_m": None,
    }

    updated = client.put(
        f"/projects/{project.id}/site-location",
        json={
            "latitude": 55.7558,
            "longitude": 37.6173,
            "label": "Стройплощадка",
            "radius_m": 150,
            "accuracy_m": 12.5,
        },
    )
    assert updated.status_code == 200
    assert location_fields(updated.json()) == {
        "configured": True,
        "latitude": 55.7558,
        "longitude": 37.6173,
        "label": "Стройплощадка",
        "radius_m": 150,
        "accuracy_m": 12.5,
    }
    assert client.get(f"/projects/{project.id}/site-location").json() == updated.json()


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": -90.001, "longitude": 37.6173, "radius_m": 100},
        {"latitude": 90.001, "longitude": 37.6173, "radius_m": 100},
        {"latitude": 55.7558, "longitude": -180.001, "radius_m": 100},
        {"latitude": 55.7558, "longitude": 180.001, "radius_m": 100},
        {"latitude": 55.7558, "longitude": 37.6173, "radius_m": 24},
        {"latitude": 55.7558, "longitude": 37.6173, "radius_m": 5001},
        {"latitude": 55.7558, "longitude": 37.6173, "radius_m": 100, "accuracy_m": -0.1},
    ],
)
def test_site_location_rejects_invalid_coordinates_radius_and_accuracy(site_location_client, payload):
    client, _, _, _, project, _ = site_location_client
    response = client.put(f"/projects/{project.id}/site-location", json=payload)
    assert response.status_code == 422


def test_site_location_is_isolated_by_project_membership(site_location_client):
    client, current_user, owner, other_owner, project, other_project = site_location_client

    assert client.put(
        f"/projects/{project.id}/site-location",
        json={"latitude": 55.7558, "longitude": 37.6173, "radius_m": 100},
    ).status_code == 200

    assert client.get(f"/projects/{other_project.id}/site-location").status_code == 403
    assert client.put(
        f"/projects/{other_project.id}/site-location",
        json={"latitude": 1, "longitude": 2, "radius_m": 100},
    ).status_code == 403

    current_user["value"] = other_owner
    untouched = client.get(f"/projects/{other_project.id}/site-location")
    assert untouched.status_code == 200
    assert untouched.json()["configured"] is False

    assert client.get(f"/projects/{project.id}/site-location").status_code == 403
    current_user["value"] = owner
    assert client.get(f"/projects/{project.id}/site-location").json()["latitude"] == 55.7558


def test_site_location_allows_viewers_to_read_but_not_overwrite(site_location_client):
    client, current_user, _, other_owner, project, _ = site_location_client
    client.put(
        f"/projects/{project.id}/site-location",
        json={"latitude": 55.7558, "longitude": 37.6173, "radius_m": 100},
    )
    # The second user becomes a read-only participant in this project.
    db = next(app.dependency_overrides[get_db]())
    db.add(ProjectMember(project_id=project.id, user_id=other_owner.id, role="viewer"))
    db.commit()
    current_user["value"] = other_owner

    assert client.get(f"/projects/{project.id}/site-location").status_code == 200
    assert client.put(
        f"/projects/{project.id}/site-location",
        json={"latitude": 1, "longitude": 2, "radius_m": 100},
    ).status_code == 403
