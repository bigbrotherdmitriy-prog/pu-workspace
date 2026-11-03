"""Read-only organization resolution for project creation.

Tenant membership is a restriction, not a grant of new workspace role powers.
Legacy organizations and global administrators retain the previous selection.
"""
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.organization_contract import Organization
from app.models.workspace_tenant import TenantWorkspaceMember

@dataclass(frozen=True)
class CreationOrganization:
    id: int
    tenant_workspace_id: int | None


def resolve_creation_organization(db: Session, user_id: int, is_admin: bool,
                                  organization_id: int | None) -> CreationOrganization:
    with db.no_autoflush:
        selected = organization_id or db.scalar(select(Organization.id).order_by(Organization.id).limit(1))
        row = db.execute(select(Organization.id, Organization.tenant_workspace_id)
                         .where(Organization.id == selected)).first() if selected is not None else None
        if row is None:
            raise LookupError('Organization is required')
        if not is_admin and row.tenant_workspace_id is not None:
            membership = db.scalar(select(TenantWorkspaceMember.id).where(
                TenantWorkspaceMember.user_id == user_id,
                TenantWorkspaceMember.workspace_id == row.tenant_workspace_id).limit(1))
            if membership is None:
                raise PermissionError('Organization access denied')
        return CreationOrganization(row.id, row.tenant_workspace_id)
