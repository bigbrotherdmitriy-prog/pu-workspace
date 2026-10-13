"""V6-04 device registration for FCM push; all tokens are synthetic."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import notification_devices
from app.api.notification_devices import router
from app.core.auth import require_user
from app.database import Base, get_db
from app.models.notification_device import UserDeviceToken
from app.models.user import User

TOKEN = "fcm-synthetic-token:APA91b" + "x" * 40


@pytest.fixture
def http_world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = User(name="Synthetic first", email="first@example.test")
        second = User(name="Synthetic second", email="second@example.test")
        db.add_all([first, second]); db.commit()
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        current = {"user": first}
        app.dependency_overrides[require_user] = lambda: current["user"]
        with TestClient(app) as client:
            yield client, db, first, second, current
    engine.dispose()


def _owners(db):
    db.expire_all()
    return [(row.fcm_token, row.user_id) for row in db.scalars(select(UserDeviceToken).order_by(UserDeviceToken.id))]


def test_register_is_idempotent_for_the_same_user(http_world):
    client, db, first, _, _ = http_world
    for _ in range(2):
        response = client.post("/api/notifications/devices/register", json={"fcm_token": TOKEN})
        assert response.status_code == 200 and response.json() == {"registered": True}
    assert _owners(db) == [(TOKEN, first.id)]


def test_register_rebinds_token_to_the_new_user(http_world):
    client, db, first, second, current = http_world
    client.post("/api/notifications/devices/register", json={"fcm_token": TOKEN})
    current["user"] = second
    assert client.post("/api/notifications/devices/register", json={"fcm_token": TOKEN}).status_code == 200
    assert _owners(db) == [(TOKEN, second.id)]


def test_revoke_removes_only_own_token(http_world):
    client, db, first, second, current = http_world
    client.post("/api/notifications/devices/register", json={"fcm_token": TOKEN})
    current["user"] = second
    foreign = client.post("/api/notifications/devices/revoke", json={"fcm_token": TOKEN})
    assert foreign.status_code == 200 and foreign.json() == {"revoked": False}
    assert _owners(db) == [(TOKEN, first.id)]
    current["user"] = first
    own = client.post("/api/notifications/devices/revoke", json={"fcm_token": TOKEN})
    assert own.json() == {"revoked": True}
    assert _owners(db) == []


@pytest.mark.parametrize("body", [
    {"fcm_token": "short"},
    {"fcm_token": "has spaces " + "x" * 30},
    {"fcm_token": "x" * 513},
    {"fcm_token": TOKEN, "platform": "ios"},
    {"fcm_token": TOKEN, "user_id": 99},
    {"fcm_token": TOKEN, "device_id": "d" * 101},
])
def test_register_rejects_malformed_payloads(http_world, body):
    client, db, *_ = http_world
    assert client.post("/api/notifications/devices/register", json=body).status_code == 422
    assert _owners(db) == []


def test_register_keeps_only_the_newest_devices(http_world, monkeypatch):
    client, db, first, *_ = http_world
    monkeypatch.setattr(notification_devices, "MAX_DEVICES_PER_USER", 2)
    for index in range(3):
        client.post("/api/notifications/devices/register", json={"fcm_token": f"{TOKEN}-{index}"})
    assert [token for token, _ in _owners(db)] == [f"{TOKEN}-1", f"{TOKEN}-2"]


def test_routes_require_an_authenticated_user():
    from app.main import app
    client = TestClient(app)
    for path in ("/api/notifications/devices/register", "/api/notifications/devices/revoke"):
        assert client.post(path, json={"fcm_token": TOKEN}).status_code == 401


def test_register_stores_optional_device_id(http_world):
    client, db, *_ = http_world
    client.post("/api/notifications/devices/register", json={"fcm_token": TOKEN, "device_id": "pixel-1"})
    client.post("/api/notifications/devices/register", json={"fcm_token": TOKEN})
    db.expire_all()
    assert db.scalar(select(UserDeviceToken)).device_id == "pixel-1"
