"""Read-only result and explicit provider reconciliation entry points."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.user import User
from app.models.v54_provider_action import ProviderAction
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import action_display_state, queue_reconciliation


router = APIRouter(prefix="/provider-actions", tags=["provider-actions"])


def _scoped_action(db: Session, action_id: str, revision: int, user: User, role: str):
    row = db.get(ProviderAction, (action_id, revision))
    if row is None:
        raise HTTPException(404, "Provider action not found")
    require_project_role(db, user, row.project_id, role)
    return row


@router.get("/{action_id}/revisions/{revision}")
def provider_action_status(action_id: str, revision: int, db: Session = Depends(get_db),
                           user: User = Depends(require_user)):
    row = _scoped_action(db, action_id, revision, user, "viewer")
    state = action_display_state(db, row.action_id)
    if state.get("revision") != revision:
        raise HTTPException(409, "Action revision is no longer current")
    return state


@router.post("/{action_id}/revisions/{revision}/reconcile")
def reconcile_provider_action(action_id: str, revision: int, db: Session = Depends(get_db),
                              user: User = Depends(require_user)):
    _scoped_action(db, action_id, revision, user, "manager")
    try:
        return queue_reconciliation(db, action_id=action_id, revision=revision, actor=user)
    except ProviderActionError as exc:
        db.rollback()
        raise HTTPException(409, f"Provider reconciliation is unavailable ({exc.code})") from exc
