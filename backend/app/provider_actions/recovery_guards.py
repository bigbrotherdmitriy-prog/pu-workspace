"""Narrow recovery fences using the existing action ledger and audit table.

Only a republication explicitly linked at confirmation is a recovery descendant.
Revision order alone is not such a link: normal edits must remain independent.
"""
from __future__ import annotations

import json
from hashlib import sha256

from sqlalchemy import select, text

from app.models.audit_log import AuditLog
from app.models.v54_provider_action import ProviderAction, ProviderExecutionAttempt, ProviderOutcomeObservation
from app.provider_actions.contracts import ProviderActionError


ABSENCE_CODE = "human_confirmed_absence_after_rotation"
LINK_EVENT = "v54.provider.recovery_republication"


def lock_action_stream(db, organization_id: int, action_id: str) -> None:
    """Same transaction-scoped key as the existing product confirmation path."""
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(sha256(f"{organization_id}:{action_id}".encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def human_absence_observation(db, row):
    latest = db.scalar(select(ProviderOutcomeObservation).where(
        ProviderOutcomeObservation.action_id == row.action_id,
        ProviderOutcomeObservation.revision == row.revision,
    ).order_by(ProviderOutcomeObservation.sequence.desc()).limit(1))
    return latest if (latest and latest.outcome == "NOT_APPLIED"
        and latest.source == "RECONCILE" and latest.safe_code == ABSENCE_CODE) else None


def has_human_absence_history(db, row) -> bool:
    return db.scalar(select(ProviderOutcomeObservation.id).where(
        ProviderOutcomeObservation.action_id == row.action_id,
        ProviderOutcomeObservation.revision == row.revision,
        ProviderOutcomeObservation.outcome == "NOT_APPLIED",
        ProviderOutcomeObservation.source == "RECONCILE",
        ProviderOutcomeObservation.safe_code == ABSENCE_CODE,
    ).limit(1)) is not None


def record_republication(db, original_row, new_row) -> None:
    """Caller holds stream lock; insert before its existing enqueue/commit."""
    if original_row is None:
        return
    evidence = human_absence_observation(db, original_row)
    if evidence is None:
        return
    if (original_row.action_id != new_row.action_id
            or original_row.organization_id != new_row.organization_id
            or original_row.project_id != new_row.project_id
            or new_row.revision != original_row.revision + 1):
        raise ProviderActionError("dispatch_binding_mismatch")
    db.add(AuditLog(action=LINK_EVENT, entity_type="provider_action", entity_id=None,
        details=json.dumps({"organization_id": original_row.organization_id,
            "action_id": original_row.action_id, "source_revision": original_row.revision,
            "revision": new_row.revision, "source_sequence": evidence.sequence,
            "source_envelope_hash": original_row.envelope_hash,
            "envelope_hash": new_row.envelope_hash}, sort_keys=True)))


def _links(db, row):
    # The existing audit table has no JSON index; no new schema is introduced.
    result = []
    for raw in db.scalars(select(AuditLog.details).where(AuditLog.action == LINK_EVENT)):
        try:
            item = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict) or item.get("organization_id") != row.organization_id or item.get("action_id") != row.action_id:
            continue
        if any(type(item.get(key)) is not int for key in ("source_revision", "revision", "source_sequence")):
            raise ProviderActionError("dispatch_binding_mismatch")
        if item["source_revision"] >= item["revision"]:
            raise ProviderActionError("dispatch_binding_mismatch")
        old = db.get(ProviderAction, (row.action_id, item["source_revision"]))
        new = db.get(ProviderAction, (row.action_id, item["revision"]))
        proof = db.scalar(select(ProviderOutcomeObservation).where(
            ProviderOutcomeObservation.action_id == row.action_id,
            ProviderOutcomeObservation.revision == item["source_revision"],
            ProviderOutcomeObservation.sequence == item["source_sequence"],
        ))
        if (not old or not new or old.organization_id != row.organization_id
                or new.organization_id != row.organization_id
                or old.envelope_hash != item.get("source_envelope_hash")
                or new.envelope_hash != item.get("envelope_hash")
                or not proof or proof.outcome != "NOT_APPLIED"
                or proof.source != "RECONCILE" or proof.safe_code != ABSENCE_CODE):
            raise ProviderActionError("dispatch_binding_mismatch")
        result.append(item)
    return result


def require_republication_dispatch(db, row) -> None:
    if row.state == "BLOCKED":
        raise ProviderActionError("unknown_requires_reconciliation")
    links = _links(db, row)
    revision = row.revision
    seen = set()
    while revision not in seen:
        seen.add(revision)
        parents = [link for link in links if link["revision"] == revision]
        if not parents:
            return
        if len(parents) != 1:
            raise ProviderActionError("dispatch_binding_mismatch")
        parent = parents[0]
        positive = db.scalar(select(ProviderOutcomeObservation.id).where(
            ProviderOutcomeObservation.action_id == row.action_id,
            ProviderOutcomeObservation.revision == parent["source_revision"],
            ProviderOutcomeObservation.sequence > parent["source_sequence"],
            ProviderOutcomeObservation.outcome == "APPLIED",
        ).limit(1))
        if positive is not None:
            raise ProviderActionError("unknown_requires_reconciliation")
        revision = parent["source_revision"]
    raise ProviderActionError("dispatch_binding_mismatch")


def fence_republications(db, original_row) -> None:
    """Preserve attempted effects; block only linked not-yet-dispatched retries."""
    links = _links(db, original_row)
    descendants = {original_row.revision}
    for link in sorted(links, key=lambda item: item["revision"]):
        if link["source_revision"] not in descendants:
            continue
        descendants.add(link["revision"])
        newer = db.scalar(select(ProviderAction).where(
            ProviderAction.action_id == original_row.action_id,
            ProviderAction.revision == link["revision"],
        ).execution_options(populate_existing=True).with_for_update())
        attempt = db.get(ProviderExecutionAttempt, (newer.action_id, newer.revision))
        if attempt is None:
            newer.state = "BLOCKED"
        db.add(AuditLog(action="v54.provider.recovery_late_positive", entity_type="provider_action",
            entity_id=None, details=json.dumps({"action_id": original_row.action_id,
                "original_revision": original_row.revision, "revision": newer.revision,
                "already_dispatched": attempt is not None}, sort_keys=True)))
