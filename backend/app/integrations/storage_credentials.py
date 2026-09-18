"""Provider-neutral credential lookup contract for storage adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration_credential import IntegrationCredential
from app.models.project import Project


CONNECTION_PREFIX = "storage-credential:"


class StorageCredentialError(RuntimeError):
    pass


class StorageCredentialUnavailable(StorageCredentialError):
    pass


class StorageCredentialScopeMismatch(StorageCredentialError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedStorageCredential:
    connection_id: str
    credential_id: int
    project_id: int
    organization_id: int
    provider: str
    capability: str
    account_external_id: str | None
    account_email: str | None
    row: IntegrationCredential = field(repr=False, compare=False)


@runtime_checkable
class StorageCredentialPort(Protocol):
    def resolve(
        self, db: Session, *, connection_id: str, project_id: int,
        organization_id: int, provider: str,
    ) -> ResolvedStorageCredential: ...

    def store(
        self, db: Session, *, project_id: int, organization_id: int,
        provider: str, access_token: str, refresh_token: str | None,
        token_uri: str, scopes: tuple[str, ...], expires_at: datetime | None,
        account_external_id: str, account_email: str | None,
    ) -> ResolvedStorageCredential: ...


def connection_id_for(credential_id: int) -> str:
    if credential_id <= 0:
        raise ValueError("credential_id must be positive")
    return f"{CONNECTION_PREFIX}{credential_id}"


def credential_id_from(connection_id: str) -> int:
    if not connection_id.startswith(CONNECTION_PREFIX):
        raise StorageCredentialUnavailable("Storage credential reference is invalid")
    raw = connection_id.removeprefix(CONNECTION_PREFIX)
    if not raw.isascii() or not raw.isdigit() or int(raw) <= 0 or str(int(raw)) != raw:
        raise StorageCredentialUnavailable("Storage credential reference is invalid")
    return int(raw)


class DatabaseStorageCredentialPort:
    """Exact credential resolver; never falls back to another project row."""

    def resolve(self, db: Session, *, connection_id: str, project_id: int,
                organization_id: int, provider: str) -> ResolvedStorageCredential:
        credential_id = credential_id_from(connection_id)
        project = db.scalar(select(Project).where(Project.id == project_id))
        if project is None or project.organization_id != organization_id:
            raise StorageCredentialScopeMismatch("Storage credential scope does not match project")
        row = db.scalar(select(IntegrationCredential).where(
            IntegrationCredential.id == credential_id,
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.provider == provider,
            IntegrationCredential.capability == "storage",
        ))
        if row is None:
            raise StorageCredentialScopeMismatch("Storage credential scope does not match connection")
        return self._resolved(row, organization_id)

    def store(self, db: Session, *, project_id: int, organization_id: int,
              provider: str, access_token: str, refresh_token: str | None,
              token_uri: str, scopes: tuple[str, ...], expires_at: datetime | None,
              account_external_id: str, account_email: str | None) -> ResolvedStorageCredential:
        project = db.scalar(select(Project).where(Project.id == project_id))
        if project is None or project.organization_id != organization_id:
            raise StorageCredentialScopeMismatch("Storage credential scope does not match project")
        row = db.scalar(select(IntegrationCredential).where(
            IntegrationCredential.project_id == project_id,
            IntegrationCredential.provider == provider,
            IntegrationCredential.capability == "storage",
        ))
        if row is None:
            row = IntegrationCredential(project_id=project_id, provider=provider, capability="storage")
            db.add(row)
            db.flush()
        row.access_token = access_token
        row.refresh_token = refresh_token or row.refresh_token
        row.token_uri = token_uri
        row.expires_at = expires_at
        row.scopes = " ".join(scopes)
        row.account_external_id = account_external_id
        row.account_email = account_email
        db.flush()
        return self._resolved(row, organization_id)

    @staticmethod
    def _resolved(row: IntegrationCredential, organization_id: int) -> ResolvedStorageCredential:
        return ResolvedStorageCredential(
            connection_id=connection_id_for(row.id), credential_id=row.id,
            project_id=row.project_id, organization_id=organization_id,
            provider=row.provider, capability=row.capability,
            account_external_id=row.account_external_id, account_email=row.account_email,
            row=row,
        )
