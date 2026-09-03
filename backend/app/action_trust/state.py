"""Business-state guard; queue retries and lease expiry cannot authorize effects."""
from app.action_trust.guards import TrustConflict


def require_new_effect(state: str) -> None:
    if state in {"EXECUTING", "UNKNOWN", "SUCCEEDED", "FAILED_NOT_APPLIED", "CANCELLED"}:
        raise TrustConflict("outcome_not_replayable")
    if state != "READY":
        raise TrustConflict("action_not_ready")
