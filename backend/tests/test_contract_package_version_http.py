from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.contract_package import router as package_router
from app.api.organizations_contracts import router as contracts_router
from app.core.auth import require_user
from app.database import Base, get_db
from app.models.audit_log import AuditLog
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.organization_contract import Contract, ContractVersion, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture
def contract_http_world():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        organization = Organization(name="Synthetic version tenant")
        owner = User(name="Synthetic owner", email="version-owner@example.test", is_admin=False)
        db.add_all([organization, owner])
        db.flush()
        project = Project(name="Synthetic version project", organization_id=organization.id)
        db.add(project)
        db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
        documents = [Document(
            project_id=project.id,
            name=f"synthetic-attachment-{index}.pdf",
            source="synthetic",
            status="ready",
        ) for index in (1, 2)]
        db.add_all(documents)
        db.commit()

        app = FastAPI()
        app.include_router(contracts_router)
        app.include_router(package_router)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_user] = lambda: owner
        with TestClient(app) as client:
            response = client.post(f"/projects/{project.id}/contracts", json={
                "number": "SYNTHETIC-1",
                "title": "Synthetic version contract",
                "contract_kind": "prime_reference",
                "amount": "1000.00",
                "advance_amount": "100.00",
                "retention_percent": "5.00",
            })
            assert response.status_code == 200, response.text
            contract_id = response.json()["id"]
            yield SimpleNamespace(
                client=client, db=db, project_id=project.id, contract_id=contract_id,
                document_ids=[document.id for document in documents],
                url=f"/projects/{project.id}/contracts/{contract_id}",
            )
    engine.dispose()


def _history(world):
    world.db.expire_all()
    return list(world.db.scalars(select(ContractVersion).where(
        ContractVersion.contract_id == world.contract_id,
    ).order_by(ContractVersion.sequence)))


def _stored_state(world):
    history = _history(world)
    contract = world.db.get(Contract, world.contract_id)
    return {
        "business_fields": {column.name: getattr(contract, column.name) for column in Contract.__table__.columns},
        "history": [(row.id, row.sequence, row.event, row.resulting_record_version,
                     row.snapshot, row.changed_fields) for row in history],
        "links": [(row.id, row.document_id, row.role) for row in world.db.scalars(
            select(ContractDocumentLink).where(ContractDocumentLink.contract_id == world.contract_id)
            .order_by(ContractDocumentLink.id)
        )],
        "audit_ids": list(world.db.scalars(select(AuditLog.id).order_by(AuditLog.id))),
    }


@pytest.mark.parametrize("endpoint", ["documents", "applications"])
def test_attachment_returns_stored_version_and_adds_one_business_snapshot(contract_http_world, endpoint):
    world = contract_http_world
    response = world.client.post(f"{world.url}/{endpoint}", json={
        "expected_record_version": 1,
        "document_ids": world.document_ids,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    history = _history(world)
    stored = world.db.get(Contract, world.contract_id)
    assert stored.record_version == 2
    assert [(row.sequence, row.event, row.resulting_record_version) for row in history] == [
        (1, "created", 1), (2, "linked", 2),
    ]
    assert history[-1].changed_fields == ["linked_document_ids"]
    assert history[-1].snapshot["linked_document_ids"] == world.document_ids
    assert stored.amount == Decimal("1000.00")
    assert stored.advance_amount == Decimal("100.00")
    assert stored.retention_percent == Decimal("5.00")
    assert body["attached"] == 2
    assert body["originals_changed"] is False
    assert body["record_version"] == stored.record_version


@pytest.mark.parametrize("endpoint", ["documents", "applications"])
def test_idempotent_attachment_returns_current_version_without_new_links_or_history(contract_http_world, endpoint):
    world = contract_http_world
    first = world.client.post(f"{world.url}/{endpoint}", json={
        "expected_record_version": 1,
        "document_ids": world.document_ids,
    })
    assert first.status_code == 200, first.text
    before = _stored_state(world)
    response = world.client.post(f"{world.url}/{endpoint}", json={
        "expected_record_version": 2,
        "document_ids": [*world.document_ids, world.document_ids[0]],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    after = _stored_state(world)
    assert after["business_fields"] == before["business_fields"]
    assert after["history"] == before["history"]
    assert after["links"] == before["links"]
    assert body["attached"] == 0
    assert body["record_version"] == after["business_fields"]["record_version"] == 2


@pytest.mark.parametrize("endpoint", ["documents", "applications"])
def test_stale_attachment_returns_409_without_business_history_or_link_writes(contract_http_world, endpoint):
    world = contract_http_world
    first = world.client.post(f"{world.url}/{endpoint}", json={
        "expected_record_version": 1,
        "document_ids": world.document_ids[:1],
    })
    assert first.status_code == 200, first.text
    before = _stored_state(world)
    stale = world.client.post(f"{world.url}/{endpoint}", json={
        "expected_record_version": 1,
        "document_ids": world.document_ids[1:],
    })
    assert stale.status_code == 409
    assert _stored_state(world) == before


def test_financial_patch_persists_exact_business_fields_and_one_snapshot(contract_http_world):
    world = contract_http_world
    payload = {
        "expected_record_version": 1,
        "amount": "1250.50",
        "advance_amount": "250.25",
        "retention_percent": "7.50",
    }
    response = world.client.patch(world.url, json=payload)
    assert response.status_code == 200, response.text
    history = _history(world)
    stored = world.db.get(Contract, world.contract_id)
    assert response.json()["record_version"] == stored.record_version == 2
    for field in ("amount", "advance_amount", "retention_percent"):
        assert getattr(stored, field) == Decimal(payload[field])
        assert history[-1].snapshot[field] == payload[field]
    assert [(row.sequence, row.event) for row in history] == [(1, "created"), (2, "updated")]
    assert history[-1].changed_fields == ["advance_amount", "amount", "retention_percent"]
    before = _stored_state(world)
    replay = world.client.patch(world.url, json={**payload, "expected_record_version": 2})
    assert replay.status_code == 200, replay.text
    assert replay.json()["record_version"] == 2
    assert _stored_state(world) == before


def test_stale_financial_patch_returns_409_without_business_or_history_writes(contract_http_world):
    world = contract_http_world
    updated = world.client.patch(world.url, json={
        "expected_record_version": 1,
        "amount": "1250.50",
        "advance_amount": "250.25",
        "retention_percent": "7.50",
    })
    assert updated.status_code == 200, updated.text
    before = _stored_state(world)
    stale = world.client.patch(world.url, json={
        "expected_record_version": 1,
        "amount": "999.99",
        "advance_amount": "99.99",
        "retention_percent": "1.00",
    })
    assert stale.status_code == 409
    assert _stored_state(world) == before
