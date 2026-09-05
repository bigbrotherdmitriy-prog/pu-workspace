"""Explicit local-upload composition for the disposable Docker CI stack only.

Production remains fail closed.  This module refuses to install a runtime unless
the database, storage root, and opt-in marker all identify the isolated CI
environment used by ``docker-compose.ci.yml``.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.v54_permissions import SyntheticPolicy
from app.core.v54_refs import ObjectRef, VersionPin
from app.database import SessionLocal
from app.local_upload_staging import (
    LocalUploadBusinessProcessor,
    LocalUploadLifecycleAdapter,
    LocalUploadRuntime,
    UploadScope,
    configure_local_upload_runtime,
)
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle


_NAMESPACE = UUID("cda49e89-786a-4fad-8d3a-2080b44f3a26")
_CI_ROOT = Path("/var/lib/pu-workspace-ci-staging")
_KEK = KekRef("local-upload", "ci-v1")
_OPERATIONS = frozenset({"write", "observe", "audit", "fragment"})


class CiLocalUploadConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _CiKekResolver:
    key: bytes

    def resolve(self, reference: str, version: str) -> bytes:
        if (reference, version) != (_KEK.reference, _KEK.version):
            raise KeyError("unknown_key")
        return self.key


def _ci_database() -> bool:
    try:
        url = make_url(os.environ["DATABASE_URL"])
    except (KeyError, ValueError):
        return False
    return (
        url.get_backend_name() == "postgresql"
        and url.host == "db"
        and url.database == "pu_test"
        and url.username == "pu_test"
    )


def _key() -> bytes:
    try:
        value = base64.urlsafe_b64decode(os.environ["TOKEN_ENCRYPTION_KEY"].encode("ascii"))
    except (KeyError, ValueError, UnicodeError) as exc:
        raise CiLocalUploadConfigurationError("invalid_ci_staging_key") from exc
    if len(value) != 32:
        raise CiLocalUploadConfigurationError("invalid_ci_staging_key")
    return value


def _policy(db, scope: UploadScope) -> SyntheticPolicy:
    project = db.scalar(select(Project).where(
        Project.id == scope.project_id,
        Project.archived_at.is_(None),
    ))
    user = db.get(User, scope.owner_id)
    membership = db.scalar(select(ProjectMember).where(
        ProjectMember.project_id == scope.project_id,
        ProjectMember.user_id == scope.owner_id,
        ProjectMember.role.in_(("manager", "owner")),
    ))
    if project is None or user is None or membership is None:
        raise CiLocalUploadConfigurationError("ci_scope_unavailable")
    tenant_id = int(project.organization_id)
    policy_id = str(uuid5(_NAMESPACE, f"{tenant_id}:{scope.project_id}"))
    tenant = {"kind": "int", "value": str(tenant_id)}
    pin = VersionPin(
        ref=ObjectRef(
            namespace="pu", type="policy", tenant_id=tenant,
            id={"kind": "uuid", "value": policy_id},
        ),
        version_kind="revision",
        value=1,
    )
    now = datetime.now(timezone.utc)
    return SyntheticPolicy(
        tenant_id=tenant_id,
        project_id=scope.project_id,
        pin=pin,
        grants=frozenset((scope.owner_id, operation) for operation in _OPERATIONS),
        accounts=frozenset(),
        namespaces=frozenset(),
        binding_epochs=(),
        valid_until=now + timedelta(hours=2),
        freshness_ttl=timedelta(minutes=5),
        authority_epoch=1,
        acl="allow",
        retention_known=True,
        residency_allowed=True,
        synthetic_only=True,
    )


def install_ci_local_upload_runtime() -> bool:
    """Install the synthetic runtime only in the isolated Docker CI stack."""
    if os.getenv("PU_LOCAL_UPLOAD_CI_RUNTIME", "false").strip().lower() != "true":
        return False
    configured_root = Path(os.getenv("PU_LOCAL_UPLOAD_STAGING_ROOT", ""))
    if not _ci_database() or configured_root != _CI_ROOT:
        raise CiLocalUploadConfigurationError("unsafe_ci_local_upload_configuration")

    max_file_bytes = int(os.getenv("LOCAL_UPLOAD_MAX_FILE_BYTES", str(10 * 1024 * 1024)))
    storage = FilesystemStagingStorage(configured_root, _CiKekResolver(_key()))

    def authority_factory(db, scope: UploadScope) -> LifecycleAuthority:
        return LifecycleAuthority(
            policy=_policy(db, scope),
            allowed_residencies=frozenset({"isolated-ci"}),
            allowed_keks=frozenset({_KEK}),
            max_retention=timedelta(hours=24),
            derive_allowed=True,
            retention_owner=True,
        )

    lifecycle = A05LocalUploadLifecycle(
        storage=storage,
        authority_factory=authority_factory,
        clock=lambda: datetime.now(timezone.utc),
        residency="isolated-ci",
        kek=_KEK,
        max_file_bytes=max_file_bytes,
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
