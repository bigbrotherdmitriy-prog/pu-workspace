from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 - register all mapped tables
from app.api.evidence import get_evidence_clock
from app.api.ai_secretary import _message_payload
from app.core.auth import require_user
from app.core.v54_authority import PILOT_SCOPE
from app.database import Base, get_db
from app.main import app
from app.models.project_member import ProjectMember
from app.models.ai_secretary import Message
from app.models.user import User
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import Evidence, SourceCurrent, SourceReference, SourceVersion
from app.source_evidence.fragment_reader import FragmentStorePayload
from test_v54_source_evidence_pilot import prepared
from v54_pilot_fixture import NOW, seed, uid


TEXT = b"Synthetic API evidence fragment."
UNAVAILABLE = {
    "schema_version": "evidence-fragment.v54.2",
    "state": "unavailable",
    "status": "unavailable",
    "reason_code": "resource_unavailable",
}


class RecordingStore:
    def __init__(self):
        self.calls = []

    def read(self, request):
        self.calls.append(request)
        return FragmentStorePayload(
            representation_id=request.representation_id,
            evidence_pin=request.evidence_pin,
            source_ref=request.source_ref,
            source_version_pin=request.source_version_pin,
            kind=request.kind,
            media_type=request.media_type,
            fragment=TEXT,
        )


def _descriptor(evidence, source, version, *, expires_at):
    return {
        "schema_version": "v54.fragment.1",
        "representation_id": "api-representation-1",
        "handle": "opaque-api-fragment-1",
        "evidence_pin": evidence.model_dump(mode="json"),
        "source_ref": source.ref.model_dump(mode="json"),
        "source_version_pin": version.model_dump(mode="json"),
        "kind": "extracted_text",
        "media_type": "text/plain; charset=utf-8",
        "retention_state": "active",
        "expires_at": expires_at.isoformat(),
    }


@pytest.fixture
def evidence_api(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'evidence-api.db'}")
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with sessions.begin() as db:
        seed(db)
        db.add(ProjectMember(project_id=4, user_id=2, role="owner"))
        db.add(AuthorityState(
            organization_id=1,
            project_id=4,
            principal_kind="user",
            principal_id="2",
            scope=PILOT_SCOPE,
            membership_role="owner",
            permissions=["fragment"],
            state="active",
            authority_epoch=7,
            record_version=1,
            valid_until=NOW + timedelta(minutes=4),
            updated_at=NOW,
            updated_by_user_id=2,
        ))
        _, source, version, evidence = prepared(db)
        db.execute(update(Evidence).where(Evidence.id == evidence.ref.id.value).values(
            representation_ref=_descriptor(
                evidence,
                source,
                version,
                expires_at=NOW + timedelta(minutes=3),
            ),
            locator={"kind": "message", "message_external_id": "synthetic-pilot-message",
                     "part": "body", "char_range": [0, 12]},
        ))

    store = RecordingStore()

    def session_override():
        with sessions() as db:
            yield db

    def user_override():
        with sessions() as db:
            return db.get(User, 2)

    app.dependency_overrides[get_db] = session_override
    app.dependency_overrides[require_user] = user_override
    app.dependency_overrides[get_evidence_clock] = lambda: (lambda: NOW)
    app.state.v54_fragment_store = store
    try:
        yield TestClient(app), sessions, evidence, source, version, store
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "v54_fragment_store"):
            del app.state.v54_fragment_store
        engine.dispose()


def test_current_fragment_projection_has_exact_pins_and_earliest_expiry(evidence_api):
    client, _, evidence, source, version, store = evidence_api
    response = client.get(f"/api/v54/evidence/{evidence.ref.id.value}/fragment?revision=1")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["schema_version"] == "evidence-fragment.v54.2"
    assert body["state"] == "readable"
    assert body["version_state"] == "current"
    assert body["valid_until"] == (NOW + timedelta(minutes=3)).isoformat().replace("+00:00", "Z")
    assert body["evidence"] == {
        "id": evidence.ref.id.value,
        "revision": 1,
        "source_id": source.ref.id.value,
        "source_version_id": version.ref.id.value,
    }
    assert body["source_version"] == {
        "id": version.ref.id.value,
        "revision": 1,
        "source_id": source.ref.id.value,
    }
    assert body["locator"]["kind"] == "message"
    assert body["fragment"]["excerpt"] == TEXT.decode()
    assert len(store.calls) == 1


def test_explicit_historical_fragment_keeps_pinned_version(evidence_api):
    client, sessions, evidence, source, version, store = evidence_api
    with sessions.begin() as db:
        historical_source = db.get(SourceReference, source.ref.id.value)
        current = SourceVersion(
            id=uid(91),
            organization_id=1,
            source_id=historical_source.id,
            observation_key="api-current-observation",
            provider_revision="api-v2",
            consistency="revision_bound",
            locator_at_observation={"kind": "opaque_id", "value": "api-current"},
            integrity=[],
            observed_at=NOW,
        )
        db.add(current)
        db.flush()
        db.get(SourceCurrent, historical_source.id).version_id = current.id

    response = client.get(f"/api/v54/evidence/{evidence.ref.id.value}/fragment?revision=1")
    assert response.status_code == 200
    assert response.json()["version_state"] == "historical"
    assert response.json()["source_version"]["id"] == version.ref.id.value
    assert response.json()["source"]["current_source_version_id"] == uid(91)
    assert len(store.calls) == 1


def test_inbox_projection_exposes_only_exact_evidence_pins(evidence_api):
    _, sessions, _, _, _, _ = evidence_api
    with sessions() as db:
        payload = _message_payload(db, db.get(Message, 6), action_provider="synthetic")

    assert payload["evidence_refs"] == [{"id": uid(16), "revision": 1}]


@pytest.mark.parametrize("denial", ["not_found", "cross_tenant", "revoked", "store_unavailable"])
def test_all_denials_are_identical_and_never_cache_or_leak(evidence_api, denial):
    client, sessions, evidence, source, _, store = evidence_api
    evidence_id = evidence.ref.id.value
    if denial == "not_found":
        evidence_id = uid(999)
    elif denial == "cross_tenant":
        with sessions.begin() as db:
            db.execute(
                update(SourceReference)
                .where(SourceReference.id == source.ref.id.value)
                .values(origin_project_id=9)
            )
    elif denial == "revoked":
        with sessions.begin() as db:
            record = db.get(Evidence, evidence_id)
            descriptor = dict(record.representation_ref)
            descriptor["retention_state"] = "purged"
            db.execute(
                update(Evidence)
                .where(Evidence.id == evidence_id)
                .values(representation_ref=descriptor)
            )
    else:
        del app.state.v54_fragment_store

    response = client.get(f"/api/v54/evidence/{evidence_id}/fragment?revision=1")
    assert response.status_code == 404
    assert response.json() == UNAVAILABLE
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert store.calls == []


@pytest.mark.parametrize("locator", [
    {"kind": "page", "page": 2},
    {"kind": "section_clause", "section_path": ["A"], "clause_label": "2", "anchor": None},
    {"kind": "sheet_cell", "sheet_key": "sheet-1", "sheet_name": "Plan", "range_a1": "B2:C3", "value_kind": "cached_value"},
    {"kind": "message", "message_external_id": "synthetic-pilot-message", "part": "body", "char_range": [0, 8]},
])
def test_http_projection_preserves_supported_locator_shapes(locator):
    from app.source_evidence.http_projection import locator_payload

    assert locator_payload(locator) == locator
