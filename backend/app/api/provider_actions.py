from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.database import get_db
from app.models.user import User
from app.provider_actions.contracts import ProviderActionError
from app.provider_actions.product import queue_reconciliation


router = APIRouter(prefix="/provider-actions", tags=["provider-actions"])


@router.post("/{action_id}/revisions/{revision}/reconcile")
def reconcile_provider_action(
    action_id: str,
    revision: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Queue authoritative lookup; never call a provider in the API process."""
    try:
        return queue_reconciliation(db, action_id=action_id, revision=revision, actor=user)
    except ProviderActionError as exc:
        db.rollback()
        raise HTTPException(409, f"Provider reconciliation is unavailable ({exc.code})") from exc
