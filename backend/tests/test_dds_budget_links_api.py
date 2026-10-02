"""HTTP contract for owner-confirmed links; all records are synthetic."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.execution_finance import router
from app.core.auth import require_user
from app.database import Base, get_db
from app.models.audit_log import AuditLog
from app.models.execution_finance import DdsBudgetLinkOperation
from app.models.user import User
from test_dds_budget_links import build_world, payload


@pytest.fixture
def http_world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(name="Synthetic owner", email="owner@example.test", is_admin=True)
        db.add(user); db.flush()
        world = build_world(db, user)
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_user] = lambda: user
        with TestClient(app) as client:
            yield client, world, app
    engine.dispose()


def test_http_preview_apply_replay_history_and_whole_undo(http_world):
    client, w, _ = http_world
    prefix = f"/execution/documents/{w.document.id}"
    audits_before = w.db.query(AuditLog).count()
    proposal = client.post(prefix + "/budget-links-preview", json=w.scope)
    assert proposal.status_code == 200 and proposal.json()["apply_allowed"]
    assert w.db.query(AuditLog).count() == audits_before
    assert w.db.query(DdsBudgetLinkOperation).count() == 0
    request = payload(w, proposal.json()).model_dump(mode="json")
    result = client.post(prefix + "/budget-links-apply", json=request)
    assert result.status_code == 200 and result.json()["link_count"] == 3
    replay = client.post(prefix + "/budget-links-apply", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"]
    history = client.get(prefix + "/budget-link-operations", params=w.scope)
    assert history.status_code == 200 and len(history.json()) == 1
    undo_url = f'/execution/budget-link-operations/{result.json()["operation_id"]}/undo'
    assert client.post(undo_url).json()["undone"] is True
    assert client.post(undo_url).json()["replayed"] is True
    assert all(row.budget_line_id is None and row.status == "proposed" for row in w.rows)
    assert client.get(prefix + "/budget-link-operations", params=w.scope).json()[0]["active_link_count"] == 0


def test_http_confirmation_and_stale_versions_are_fail_closed(http_world):
    client, w, _ = http_world
    endpoint = f"/execution/documents/{w.document.id}/budget-links-apply"
    request = payload(w).model_dump(mode="json")
    assert client.post(endpoint, json={**request, "owner_confirmed": False}).status_code == 409
    w.rows[1].record_version += 1; w.db.commit()
    response = client.post(endpoint, json=request)
    assert response.status_code == 409 and "CASH_FLOW_VERSION_MISMATCH" in response.json()["detail"]
    assert all(row.budget_line_id is None for row in w.rows)


@pytest.mark.parametrize("extra", [{"planned_amount": "9.99"}, {"status": "approved"}, {"budget_line_id": 999}])
def test_http_client_cannot_send_financial_fields_or_arbitrary_targets(http_world, extra):
    client, w, _ = http_world
    response = client.post(f"/execution/documents/{w.document.id}/budget-links-apply",
                           json={**payload(w).model_dump(mode="json"), **extra})
    assert response.status_code == 422
    assert all(row.budget_line_id is None for row in w.rows)


def test_http_history_requires_valid_scope_and_authentication(http_world):
    client, w, app = http_world
    endpoint = f"/execution/documents/{w.document.id}/budget-link-operations"
    assert client.get(endpoint, params={**w.scope, "plan_year": 1900}).status_code == 422
    def anonymous():
        raise HTTPException(401, "Not authenticated")
    app.dependency_overrides[require_user] = anonymous
    assert client.get(endpoint, params=w.scope).status_code == 401
    assert client.post(f"/execution/documents/{w.document.id}/budget-links-preview", json=w.scope).status_code == 401
