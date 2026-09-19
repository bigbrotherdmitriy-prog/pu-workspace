"""Production composition for durable encrypted local uploads.

The HTTP request may select only its project and file content.  Residency,
retention, key material and live project authority are server-owned here.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.auth import ROLE_LEVEL
from app.core.v54_permissions import SourceEvidenceError
from app.core.v54_refs import VersionPin
from app.database import SessionLocal
from app.local_upload_staging import (
    LocalUploadBusinessProcessor,
    LocalUploadLifecycleAdapter,
    LocalUploadRuntime,
    UploadScope,
    configure_local_upload_runtime,
)
from app.models.materialization import Materialization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle


_NAMESPACE = UUID("f088bc3c-7446-4101-a9ad-f2f16a3b8058")
_ROOT = Path("/var/lib/pu-workspace-local-upload")
_RESIDENCY = "primary-local"
_KEK = KekRef("local-upload", "primary-v1")
_OPERATIONS = frozenset({"write", "observe", "audit", "fragment"})


class ProductionLocalUploadConfigurationError(RuntimeError):
    pass


def _deny() -> NoReturn:
    raise SourceEvidenceError("resource_unavailable")


@dataclass(frozen=True, slots=True)
class ProjectMembershipPolicy:
    """Live project RBAC projected into the materialization policy seam."""

    tenant_id: int
    project_id: int
    pin: VersionPin

    def require(self, db, scope, operation: str, now: datetime, *, lock: bool = False):
        try:
            tenant_id = int(scope.tenant.value)
            project_id = int(scope.project.id.value)
            actor_id = int(scope.actor.id.value)
            correlation_id = str(UUID(scope.correlation_id))
        except (AttributeError, TypeError, ValueError):
            _deny()
        if (
            not db.in_transaction()
            or operation not in _OPERATIONS
            or not isinstance(now, datetime)
            or now.tzinfo is None
            or tenant_id != self.tenant_id
            or project_id != self.project_id
            or scope.actor.type != "user"
            or correlation_id != scope.correlation_id
        ):
            _deny()
        project_query = select(Project).where(
            Project.id == self.project_id,
            Project.organization_id == self.tenant_id,
            Project.archived_at.is_(None),
        ).execution_options(populate_existing=True)
        if lock:
            project_query = project_query.with_for_update()
        project = db.scalar(project_query)
        user = db.get(User, actor_id)
        if project is None or user is None:
            _deny()
        if not user.is_admin:
            member_query = select(ProjectMember).where(
                ProjectMember.project_id == self.project_id,
                ProjectMember.user_id == actor_id,
            ).execution_options(populate_existing=True)
            if lock:
                member_query = member_query.with_for_update()
            member = db.scalar(member_query)
            if member is None or ROLE_LEVEL.get(member.role, 0) < ROLE_LEVEL["manager"]:
                _deny()
        return now + timedelta(minutes=5)

    def policy_pins(self) -> dict:
        return {
            key: self.pin.model_dump(mode="json")
            for key in ("access", "retention", "residency")
        }


@dataclass(frozen=True, slots=True)
class ProductionRetentionAuthority:
    service_principal: str
    allowed_residencies: frozenset[str]
    allowed_keks: frozenset[KekRef]

    def require(self, db, row: Materialization) -> None:
        project = db.scalar(select(Project).where(
            Project.id == row.project_id,
            Project.organization_id == row.organization_id,
            Project.archived_at.is_(None),
        ).with_for_update())
        if (
            project is None
            or row.residency not in self.allowed_residencies
            or KekRef(row.kek_reference, row.kek_version) not in self.allowed_keks
        ):
            _deny()


@dataclass(frozen=True, slots=True)
class _ProductionKekResolver:
    key: bytes

    def resolve(self, reference: str, version: str) -> bytes:
        if (reference, version) != (_KEK.reference, _KEK.version):
            raise KeyError("unknown_key")
        return self.key


def _database_is_production_postgres() -> bool:
    try:
        url = make_url(os.environ["DATABASE_URL"])
    except (KeyError, ValueError):
        return False
    return (
        url.get_backend_name() == "postgresql"
        and url.database not in {None, "pu_test"}
        and url.username not in {None, "pu_test"}
    )


def _key() -> bytes:
    try:
        value = base64.urlsafe_b64decode(os.environ["TOKEN_ENCRYPTION_KEY"].encode("ascii"))
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ProductionLocalUploadConfigurationError("invalid_local_upload_staging_key") from exc
    if len(value) != 32:
        raise ProductionLocalUploadConfigurationError("invalid_local_upload_staging_key")
    return value


def _authority(db, scope: UploadScope) -> LifecycleAuthority:
    project = db.scalar(select(Project).where(
        Project.id == scope.project_id,
        Project.archived_at.is_(None),
    ))
    if project is None:
        _deny()
    tenant_id = int(project.organization_id)
    policy_id = str(uuid5(_NAMESPACE, f"{tenant_id}:{scope.project_id}"))
    tenant = {"kind": "int", "value": str(tenant_id)}
    policy = ProjectMembershipPolicy(
        tenant_id=tenant_id,
        project_id=scope.project_id,
        pin=VersionPin(
            ref={
                "namespace": "pu", "type": "policy", "tenant_id": tenant,
                "id": {"kind": "uuid", "value": policy_id},
            },
            version_kind="revision",
            value=1,
        ),
    )
    return LifecycleAuthority(
        policy=policy,
        allowed_residencies=frozenset({_RESIDENCY}),
        allowed_keks=frozenset({_KEK}),
        max_retention=timedelta(hours=24),
        derive_allowed=True,
        retention_owner=True,
    )


def install_production_local_upload_runtime() -> bool:
    mode = os.getenv("PU_LOCAL_UPLOAD_RUNTIME", "").strip().lower()
    if mode != "production":
        return False
    root = Path(os.getenv("PU_LOCAL_UPLOAD_STAGING_ROOT", ""))
    if not _database_is_production_postgres() or root != _ROOT:
        raise ProductionLocalUploadConfigurationError("unsafe_production_local_upload_configuration")
    max_file_bytes = int(os.getenv("LOCAL_UPLOAD_MAX_FILE_BYTES", str(10 * 1024 * 1024)))
    storage = FilesystemStagingStorage(root, _ProductionKekResolver(_key()))
    retention = ProductionRetentionAuthority(
        service_principal="pu-local-upload-retention",
        allowed_residencies=frozenset({_RESIDENCY}),
        allowed_keks=frozenset({_KEK}),
    )
    lifecycle = A05LocalUploadLifecycle(
        storage=storage,
        authority_factory=_authority,
        clock=lambda: datetime.now(timezone.utc),
        residency=_RESIDENCY,
        kek=_KEK,
        max_file_bytes=max_file_bytes,
        retention_authority=retention,
    )
    configure_local_upload_runtime(LocalUploadRuntime(
        storage=storage,
        lifecycle=LocalUploadLifecycleAdapter(lifecycle),
        processor=LocalUploadBusinessProcessor(),
        session_factory=SessionLocal,
        kek=_KEK,
        max_file_bytes=max_file_bytes,
    ))
    return True
