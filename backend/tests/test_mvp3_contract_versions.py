from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest
from fastapi import HTTPException

import app.models  # noqa: F401
from app.api.organizations_contracts import (
    ContractCreate,
    ContractDelete,
    ContractLinkUpdate,
    create_contract,
    delete_contract,
    update_contract_links,
)
from app.database import Base
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.organization_contract import Contract, ContractVersion, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User


@pytest.fixture()
def world():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org = Organization(name="Synthetic tenant")
        owner = User(name="Owner", email="owner@synthetic.test", is_admin=False)
        outsider = User(name="Other", email="other@synthetic.test", is_admin=False)
        db.add_all([org, owner, outsider]); db.flush()
        project = Project(name="Synthetic project", organization_id=org.id)
        other_project = Project(name="Other project", organization_id=org.id)
        db.add_all([project, other_project]); db.flush()
        db.add_all([
            ProjectMember(project_id=project.id, user_id=owner.id, role="owner"),
            ProjectMember(project_id=other_project.id, user_id=outsider.id, role="owner"),
        ]); db.commit()
        yield db, project, other_project, owner, outsider
    engine.dispose()


def test_create_edit_replay_and_stale_cas_keep_immutable_snapshots(world):
    db, project, _other, owner, _outsider = world
    created = create_contract(project.id, ContractCreate(number="D-1", title="Draft", status="draft"), db, owner)
    assert created["record_version"] == 1
    assert [(v.sequence, v.event, v.snapshot["title"]) for v in db.scalars(
        select(ContractVersion).where(ContractVersion.contract_id == created["id"])
    )] == [(1, "created", "Draft")]

    edited = update_contract_links(project.id, created["id"], ContractLinkUpdate(
        expected_record_version=1, title="Signed contract", status="active",
    ), db, owner)
    assert edited["record_version"] == 2
    assert [v["event"] for v in edited["version_history"]] == ["created", "updated"]

    replay = update_contract_links(project.id, created["id"], ContractLinkUpdate(
        expected_record_version=2, title="Signed contract", status="active",
    ), db, owner)
    assert replay["record_version"] == 2
    assert len(replay["version_history"]) == 2

    with pytest.raises(HTTPException) as stale:
        update_contract_links(project.id, created["id"], ContractLinkUpdate(
            expected_record_version=1, title="Stale overwrite",
        ), db, owner)
    assert stale.value.status_code == 409
    assert db.get(Contract, created["id"]).title == "Signed contract"


def test_link_and_archive_are_versioned_and_source_evidence_survives(world):
    db, project, _other, owner, _outsider = world
    document = Document(project_id=project.id, name="contract.pdf", source="synthetic", status="ready")
    db.add(document); db.commit()
    created = create_contract(project.id, ContractCreate(number="D-2", title="Contract"), db, owner)
    linked = update_contract_links(project.id, created["id"], ContractLinkUpdate(
        expected_record_version=1, source_document_id=document.id,
    ), db, owner)
    archived = update_contract_links(project.id, created["id"], ContractLinkUpdate(
        expected_record_version=2, status="archived",
    ), db, owner)

    assert linked["version_history"][-1]["event"] == "linked"
    assert archived["record_version"] == 3
    assert archived["version_history"][-1]["event"] == "archived"
    assert archived["linked_documents"][0]["id"] == document.id
    assert db.get(Document, document.id) is document
    with pytest.raises(HTTPException) as blocked:
        delete_contract(project.id, created["id"], ContractDelete(confirmation="D-2", expected_record_version=3), db, owner)
    assert blocked.value.status_code == 409
    assert db.get(Document, document.id) is document
    assert db.scalar(select(ContractVersion).where(
        ContractVersion.contract_id == created["id"], ContractVersion.sequence == 3,
    )) is not None


def test_only_empty_draft_can_be_deleted_and_history_and_audit_remain(world):
    db, project, _other, owner, _outsider = world
    active = create_contract(project.id, ContractCreate(
        number="D-3", title="Active", contract_kind="prime_reference",
    ), db, owner)
    with pytest.raises(HTTPException) as blocked:
        delete_contract(project.id, active["id"], ContractDelete(confirmation="D-3", expected_record_version=1), db, owner)
    assert blocked.value.status_code == 409

    draft = create_contract(project.id, ContractCreate(
        number="ERR-1", title="Erroneous empty draft", status="draft", contract_kind="prime_reference",
    ), db, owner)
    result = delete_contract(project.id, draft["id"], ContractDelete(confirmation="ERR-1", expected_record_version=1), db, owner)
    assert result["deleted"] == draft["id"]
    assert db.get(Contract, draft["id"]) is None
    history = list(db.scalars(select(ContractVersion).where(
        ContractVersion.contract_id == draft["id"],
    ).order_by(ContractVersion.sequence)))
    assert [row.event for row in history] == ["created", "deleted"]
    assert db.scalar(select(AuditLog).where(
        AuditLog.action == "contract_deleted", AuditLog.entity_id == project.id,
    )) is not None


def test_contract_mutation_is_project_scoped(world):
    db, project, other_project, owner, outsider = world
    created = create_contract(project.id, ContractCreate(
        number="D-4", title="Tenant scoped", status="draft", contract_kind="prime_reference",
    ), db, owner)
    with pytest.raises(HTTPException):
        update_contract_links(other_project.id, created["id"], ContractLinkUpdate(
            expected_record_version=1, title="Cross project",
        ), db, outsider)
    assert db.get(Contract, created["id"]).title == "Tenant scoped"


def test_contract_versions_are_immutable(world):
    db, project, _other, owner, _outsider = world
    created = create_contract(project.id, ContractCreate(
        number="D-5", title="Immutable", status="draft", contract_kind="prime_reference",
    ), db, owner)
    version = db.scalar(select(ContractVersion).where(ContractVersion.contract_id == created["id"]))
    version.event = "tampered"
    with pytest.raises(ValueError, match="immutable_contract_version"):
        db.flush()


def test_pre_migration_contract_gets_baseline_before_first_edit(world):
    db, project, _other, owner, _outsider = world
    row = Contract(project_id=project.id, number="LEGACY", title="Imported", status="draft")
    db.add(row); db.commit()
    updated = update_contract_links(project.id, row.id, ContractLinkUpdate(
        expected_record_version=1, title="Imported and reviewed",
    ), db, owner)
    assert [item["event"] for item in updated["version_history"]] == ["baseline", "updated"]
    assert updated["version_history"][0]["snapshot"]["title"] == "Imported"


def test_contract_card_sends_cas_and_shows_version_history():
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "expected_record_version: expected" in source
    assert "expected_record_version: item.record_version" in source
    assert "снимков истории" in source
