"""Side-effect-free retry admission; this does not dispatch or approve mail."""
from fastapi import HTTPException


def require_mail_retry(draft, revision: int) -> None:
    if draft.status == "unknown":
        raise HTTPException(409, "mail_send_outcome_unknown_reconciliation_required")
    if draft.status != "failed":
        raise HTTPException(409, "mail_retry_not_available")
    if draft.revision != revision or draft.approved_revision != draft.revision:
        raise HTTPException(409, "mail_current_revision_not_approved")
