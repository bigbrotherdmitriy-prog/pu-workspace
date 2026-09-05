from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register every FK target
from app.contract_evidence import extract_contract_evidence, persist_contract_evidence
from app.database import Base
from app.document_engine import index_documents
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.drive_connection import DriveConnection
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.v54_pilot import ConnectionIdentity, SourceCurrent, SourceReference, SourceVersion
from app.core.integration_types import StorageObject


CONTRACT_TEXT = (
    "Договор № BRIDGE-01 от 01.09.2026.\n"
    "Цена настоящего договора составляет 1 000 000 руб.\n"
)


def _database(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'bridge.sqlite3'}")
    Base.metadata.create_all(engine)
    return engine


def _project(db: Session, provider: str) -> Project:
    organization = Organization(name=f"Синтетическая {provider}")
    db.add(organization)
    db.flush()
    project = Project(name=f"Проект {provider}", organization_id=organization.id)
    db.add(project)
    db.flush()
    db.add(DriveConnection(
        project_id=project.id,
        provider=provider,
        connection_id=f"synthetic-{provider}-connection",
        account_email=f"{provider}@example.test",
        root_folder_id="root" if provider == "google_drive" else "disk:/Customer/Project",
        status="connected",
    ))
    db.commit()
    return project


def _file(provider: str, *, content: str = CONTRACT_TEXT, versioned: bool = True) -> StorageObject:
    return StorageObject(
        id="google-file-opaque" if provider == "google_drive" else "disk:/Customer/Project/contract.txt",
        name="contract.txt",
        mime_type="text/plain",
        parent_id="google-parent" if provider == "google_drive" else "disk:/Customer/Project",
        md5_checksum="0123456789abcdef0123456789abcdef" if versioned else None,
        modified_time="2026-09-01T10:00:00Z" if versioned else None,
        content_text=content,
        object_type="file",
        provider=provider,
    )


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_provider_ingestion_binds_exact_document_version_and_replay_after_restart(tmp_path, provider):
    engine = _database(tmp_path)
    source_name = f"{provider}_snapshot"
    with Session(engine) as first:
        project = _project(first, provider)
        project_id, organization_id = project.id, project.organization_id
        documents = index_documents(first, project_id, [_file(provider)], source_name)
        document_id = documents[0].id

        document_version = first.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == 1,
        ))
        exact = first.scalar(select(SourceVersion).where(
            SourceVersion.legacy_document_version_id == document_version.id,
        ))
        assert exact is not None
        source = first.get(SourceReference, exact.source_id)
        identity = first.get(ConnectionIdentity, source.identity_id)
        current = first.get(SourceCurrent, source.id)
        assert source.organization_id == organization_id
        assert source.origin_project_id == project_id
        assert identity.provider == provider
        assert exact.consistency == "digest_observed"
        assert exact.locator_at_observation == {
            "kind": "legacy_document_version",
            "document_id": document_id,
            "document_version_id": document_version.id,
            "provider": provider,
            "source": source_name,
        }
        assert current.version_id == exact.id
        source_id, version_id = source.id, exact.id

    # A new DB session models API/worker restart. Exact delivery must converge
    # without a second source, observation or legacy document version.
    with Session(engine) as restarted:
        index_documents(restarted, project_id, [_file(provider)], source_name)
        assert restarted.scalar(select(func.count()).select_from(DocumentVersion)) == 1
        assert restarted.scalar(select(func.count()).select_from(SourceReference)) == 1
        assert restarted.scalar(select(func.count()).select_from(SourceVersion)) == 1
        assert restarted.get(SourceReference, source_id) is not None
        assert restarted.get(SourceVersion, version_id).legacy_document_version_id is not None
    engine.dispose()


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_changed_provider_content_creates_a_new_exact_immutable_observation(tmp_path, provider):
    engine = _database(tmp_path)
    with Session(engine) as db:
        project = _project(db, provider)
        source_name = f"{provider}_snapshot"
        document = index_documents(db, project.id, [_file(provider)], source_name)[0]
        first = db.scalar(select(SourceVersion).where(
            SourceVersion.legacy_document_version_id.is_not(None),
        ))

        changed = _file(provider, content=CONTRACT_TEXT + "Аванс 100 000 руб.\n")
        changed.md5_checksum = "abcdef0123456789abcdef0123456789"
        changed.modified_time = "2026-09-02T10:00:00Z"
        index_documents(db, project.id, [changed], source_name)

        versions = list(db.scalars(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
        ).order_by(DocumentVersion.version_number)))
        exact = list(db.scalars(select(SourceVersion).where(
            SourceVersion.source_id == first.source_id,
        ).order_by(SourceVersion.observed_at, SourceVersion.id)))
        assert len(versions) == 2
        assert len(exact) == 2
        assert {row.legacy_document_version_id for row in exact} == {row.id for row in versions}
        assert db.get(SourceCurrent, first.source_id).version_id == exact[-1].id
        assert first.legacy_document_version_id == versions[0].id
    engine.dispose()


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_missing_provider_revision_is_fail_closed_and_requires_manual_review(tmp_path, provider):
    engine = _database(tmp_path)
    with Session(engine) as db:
        project = _project(db, provider)
        document = index_documents(
            db, project.id, [_file(provider, versioned=False)], f"{provider}_snapshot",
        )[0]
        version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id))
        assert db.scalar(select(SourceVersion).where(
            SourceVersion.legacy_document_version_id == version.id,
        )) is None

        result = persist_contract_evidence(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            document_version=version,
            extraction=extract_contract_evidence(CONTRACT_TEXT),
        )
        assert result["status"] == "manual_review_required"
        assert result["reason_codes"] == ["exact_source_version_unavailable"]
        assert result["evidence"] == []
    engine.dispose()


@pytest.mark.parametrize(
    ("provider", "source_name", "namespace", "external_id"),
    [
        ("local_upload", "local_upload", "local-upload", "local:0123456789abcdef"),
        ("google_workspace", "gmail", "gmail", "gmail-staging:0123456789abcdef"),
    ],
)
def test_staged_ingestion_derives_exact_document_source_without_moving_origin_current(
    tmp_path, provider, source_name, namespace, external_id,
):
    engine = _database(tmp_path)
    with Session(engine) as db:
        organization = Organization(name=f"Синтетическая {provider}")
        db.add(organization)
        db.flush()
        project = Project(name=f"Проект {provider}", organization_id=organization.id)
        db.add(project)
        db.flush()
        identity = ConnectionIdentity(
            organization_id=organization.id,
            provider=provider,
            account_key=f"synthetic-{provider}",
            state="verified",
            binding_epoch=1,
            record_version=1,
            credential_generation=1,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(identity)
        db.flush()
        origin = SourceReference(
            organization_id=organization.id,
            origin_project_id=project.id,
            identity_id=identity.id,
            namespace=namespace,
            external_id=f"synthetic-{source_name}",
            external_id_kind="stable_id",
            incarnation=1,
            object_kind="attachment" if source_name == "gmail" else "file",
            canonical_locator={"kind": "opaque_id", "value": "synthetic"},
            freshness="fresh",
            sync_state="current",
            availability="available",
            last_seen_at=datetime.now(timezone.utc),
            last_checked_at=datetime.now(timezone.utc),
            policy_pins={"access": {"kind": "project_acl"}},
            residency={"source_location": "encrypted_staging", "assurance": "synthetic"},
        )
        db.add(origin)
        db.flush()
        origin_version = SourceVersion(
            organization_id=organization.id,
            source_id=origin.id,
            observation_key=f"synthetic-{source_name}-v1",
            provider_revision=None,
            consistency="metadata_only" if source_name == "gmail" else "digest_observed",
            locator_at_observation={"kind": "opaque_staging"},
            integrity=[],
            observed_at=datetime.now(timezone.utc),
        )
        db.add(origin_version)
        db.flush()
        db.add(SourceCurrent(
            source_id=origin.id,
            organization_id=organization.id,
            version_id=origin_version.id,
        ))
        db.commit()

        item = StorageObject(
            id=external_id,
            name="staged.txt",
            mime_type="text/plain",
            parent_id="staging",
            content_text=CONTRACT_TEXT,
            object_type="file",
        )
        document = index_documents(
            db,
            project.id,
            [item],
            source_name,
            exact_source_versions={item.id: origin_version.id},
        )[0]
        document_version = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
        ))
        exact = db.scalar(select(SourceVersion).where(
            SourceVersion.legacy_document_version_id == document_version.id,
        ))
        derived = db.get(SourceReference, exact.source_id)
        assert derived.parent_source_id == origin.id
        assert derived.id != origin.id
        assert exact.consistency == "digest_observed"
        assert db.get(SourceCurrent, origin.id).version_id == origin_version.id
        assert db.get(SourceCurrent, derived.id).version_id == exact.id
    engine.dispose()
