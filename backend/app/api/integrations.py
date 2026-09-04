import re

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.integrations.catalog import GOOGLE_CAPABILITIES, project_integration_catalog
from app.mailbox_identity.dto import MailboxRolloutResult, MailboxRolloutTransition
from app.mailbox_identity.service import MailboxConflict, MailboxIdentityService
from app.models.user import User

router = APIRouter(prefix="/integrations", tags=["integrations"])


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
