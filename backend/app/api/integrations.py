import re

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.integrations.catalog import GOOGLE_CAPABILITIES, project_integration_catalog
from app.mailbox_identity.dto import MailboxRolloutResult, MailboxRolloutTransition
from app.mailbox_identity.dto import MailboxAuthorityRenewal
from app.mailbox_identity.dto import MailboxCohortJoin, MailboxCohortJoinResult
from app.mailbox_identity.authority import renew_project_mailbox_authority
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService
from app.models.user import User

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.post("/mailbox-authority/renew")
def renew_mailbox_authority(
    command: MailboxAuthorityRenewal, response: Response,
    if_match: str = Header(alias="If-Match"),
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    try:
        result = renew_project_mailbox_authority(
            db, command, actor=user, expected_version=_if_match_version(if_match))
        db.commit()
    except (MailboxConflict, ValueError):
        db.rollback()
        raise HTTPException(409, "resource_unavailable") from None
    response.headers["ETag"] = f'"{result["authority_version"]}"'
    return result


def _if_match_version(value: str) -> int:
    match = re.fullmatch(r'"([1-9][0-9]*)"', value or "")
    if not match:
        raise ValueError("resource_unavailable")
    return int(match.group(1))

@router.get("/project")
def project_integrations(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    adapters = project_integration_catalog(project_id, db)
    return {"project_id": project_id, "adapters": [item.as_dict() for item in adapters]}


@router.patch("/mailbox-rollout", response_model=MailboxRolloutResult)
def change_mailbox_rollout(
    command: MailboxRolloutTransition,
    response: Response,
    if_match: str = Header(alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        expected_version = _if_match_version(if_match)
        result = MailboxIdentityService().change_rollout_flags(
            db, command, actor=user, expected_record_version=expected_version
        )
        db.commit()
    except (MailboxConflict, ValueError):
        db.rollback()
        raise HTTPException(409, "resource_unavailable") from None
    response.headers["ETag"] = f'"{result.record_version}"'
    return result


@router.post("/mailbox-rollout/join", response_model=MailboxCohortJoinResult)
def join_mailbox_rollout(
    command: MailboxCohortJoin,
    response: Response,
    if_match: str = Header(alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    try:
        authority_version = _if_match_version(if_match)
        cohort = MailboxIdentityService().join_project_cohort(
            db,
            organization_id=command.organization_id,
            project_id=command.project_id,
            mail_connection_id=str(command.mail_connection_id),
            credential_generation=command.credential_generation,
            binding_epoch=command.binding_epoch,
            actor=user,
            authority_version=authority_version,
        )
        db.commit()
    except (MailboxConflict, ValueError):
        db.rollback()
        raise HTTPException(409, "resource_unavailable") from None
    response.headers["ETag"] = f'"{cohort.record_version}"'
    return MailboxCohortJoinResult(
        id=cohort.id,
        project_id=cohort.project_id,
        mail_connection_id=cohort.mail_connection_id,
        credential_generation=cohort.credential_generation,
        enabled=cohort.enabled,
        record_version=cohort.record_version,
    )
