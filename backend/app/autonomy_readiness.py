"""Read-only, project-scoped projection of the narrow product AUTO pilot."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.autonomy_policy import (
    CREATE_INTERNAL_NOTIFICATION, CREATE_INTERNAL_TASK, SEND_EXTERNAL_MESSAGE,
    validate_stored_rules,
)
from app.core.v54_authority import PILOT_OPERATIONS, PILOT_SCOPE
from app.mailbox_identity.runtime import rollout_flags_are_valid
from app.models.audit_log import AuditLog
from app.models.job import BackgroundJob
from app.models.mailbox_identity import (
    MailboxCredentialGeneration, MailboxCutoverFlags, MailboxProjectCohort,
)
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import (
    ActionPolicy, ActionReceipt, ActionRevision, ConnectionIdentity, MailConnection,
    PendingDispatch, PilotAction,
)
from app.pilot_dispatch import PRODUCT_KIND
from app.pilot_incidents import ALERT_AUDIT_ACTION
from app.pilot_product import load_product_pilot_settings


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _remaining(value, now):
    return max(0, int((_aware(value) - now).total_seconds())) if value else 0


def exhausted_quota_reasons(quotas):
    reasons = []
    for key, usage in quotas.items():
        used = usage.get("used", usage.get("max_used", 0))
        if usage["limit"] is not None and used >= usage["limit"]:
            reasons.append(f"{key}_exhausted")
    return reasons


def _policy_for_project(db, project):
    matches = []
    for row in db.scalars(select(ActionPolicy).where(
        ActionPolicy.organization_id == project.organization_id,
    )):
        try:
            rules = validate_stored_rules(row.rules)
            if (int(rules["project_ref"]["id"]["value"]) == project.id
                    and rules["organization_id"] == project.organization_id):
                matches.append((row, rules))
        except (KeyError, TypeError, ValueError):
            continue
    if not matches:
        return None, None, []
    row, rules = max(matches, key=lambda item: item[0].revision)
    history = [{
        "revision": item.revision,
        "hash_prefix": item.policy_hash[:12],
        "enabled": stored["enabled"],
        "task_mode": stored["action_modes"][CREATE_INTERNAL_TASK],
        "notification_mode": stored["action_modes"].get(CREATE_INTERNAL_NOTIFICATION, "CONFIRM"),
        "changed_at": stored["changed_at"],
        "valid_until": _aware(item.valid_until),
    } for item, stored in sorted(matches, key=lambda match: match[0].revision, reverse=True)]
    return row, rules, history


def _runtime_projection(project_id):
    try:
        settings = load_product_pilot_settings()
    except ValueError:
        return None, "invalid"
    if not settings.enabled:
        return settings, "off"
    return settings, "matching" if settings.project_id == project_id else "mismatch"


def _mailbox(db, project):
    cohorts = list(db.scalars(select(MailboxProjectCohort).where(
        MailboxProjectCohort.organization_id == project.organization_id,
        MailboxProjectCohort.project_id == project.id,
        MailboxProjectCohort.enabled.is_(True),
    )))
    if len(cohorts) != 1:
        return {"state": "missing" if not cohorts else "ambiguous", "ready": False}
    cohort = cohorts[0]
    mail = db.get(MailConnection, cohort.mail_connection_id)
    identity = db.get(ConnectionIdentity, mail.identity_id) if mail else None
    generation = db.scalar(select(MailboxCredentialGeneration).where(
        MailboxCredentialGeneration.organization_id == project.organization_id,
        MailboxCredentialGeneration.connection_identity_id == (identity.id if identity else ""),
        MailboxCredentialGeneration.generation == cohort.credential_generation,
    ))
    flags = db.scalar(select(MailboxCutoverFlags).where(
        MailboxCutoverFlags.organization_id == project.organization_id,
        MailboxCutoverFlags.mail_connection_id == cohort.mail_connection_id,
        MailboxCutoverFlags.credential_generation == cohort.credential_generation,
    ))
    valid = bool(
        mail and mail.state == "active" and identity and identity.state == "verified"
        and identity.credential_generation == cohort.credential_generation
        and generation and generation.state == "active"
        and generation.binding_epoch == identity.binding_epoch
        and flags and rollout_flags_are_valid(flags)
    )
    producer_ready = bool(
        valid and flags.shadow_write and flags.shadow_read_compare and flags.pilot_write
        and not flags.primary_read and not flags.actions
    )
    return {
        "state": "producer_ready" if producer_ready else ("valid_not_pilot" if valid else "stale"),
        "valid": valid,
        "producer_ready": producer_ready,
        "ready": producer_ready,
        "credential_generation": cohort.credential_generation,
        "cohort_record_version": cohort.record_version,
        "cutover": ({name: bool(getattr(flags, name)) for name in (
            "shadow_write", "shadow_read_compare", "pilot_write", "primary_read", "actions"
        )} if flags else None),
    }


def _operations(db, project, policy, settings, now):
    actions = list(db.scalars(select(PilotAction).where(
        PilotAction.organization_id == project.organization_id,
        PilotAction.project_id == project.id,
    )))
    action_ids = [row.id for row in actions]
    states = Counter(row.business_state for row in actions)
    receipts = list(db.scalars(select(ActionReceipt).where(
        ActionReceipt.organization_id == project.organization_id,
        ActionReceipt.action_id.in_(action_ids),
    ))) if action_ids else []
    outcomes = Counter(row.outcome for row in receipts)
    pending = list(db.scalars(select(PendingDispatch).where(
        PendingDispatch.organization_id == project.organization_id,
        PendingDispatch.action_id.in_(action_ids),
    ))) if action_ids else []
    job_ids = [row.job_id for row in pending if row.job_id]
    jobs = list(db.scalars(select(BackgroundJob).where(
        BackgroundJob.id.in_(job_ids), BackgroundJob.kind == PRODUCT_KIND,
    ))) if job_ids else []
    job_states = Counter(row.status for row in jobs)
    alerted = set(db.scalars(select(AuditLog.entity_id).where(
        AuditLog.action == ALERT_AUDIT_ACTION,
        AuditLog.entity_type == "background_job",
        AuditLog.entity_id.in_(job_ids),
    ))) if job_ids else set()
    incidents = [{
        "job_id": row.id, "status": row.status, "created_at": row.created_at,
        "alerted": row.id in alerted,
    } for row in jobs if row.status in {"failed", "dead_letter"}]
    for action in actions:
        if action.business_state == "UNKNOWN":
            incidents.append({"action_id": action.id, "status": "UNKNOWN", "alerted": False})

    quotas = {
        "task_hourly": {"used": 0, "limit": getattr(settings, "hourly_quota", None)},
        "notification_project_hourly": {
            "used": 0, "limit": getattr(settings, "notification_project_hourly_quota", None),
        },
        "notification_recipient_daily": {
            "max_used": 0, "limit": getattr(settings, "notification_recipient_daily_quota", None),
        },
    }
    recipients = Counter()
    if policy and action_ids:
        revisions = list(db.scalars(select(ActionRevision).where(
            ActionRevision.organization_id == project.organization_id,
            ActionRevision.action_id.in_(action_ids),
            ActionRevision.policy_id == policy.id,
            ActionRevision.policy_revision == policy.revision,
            ActionRevision.created_at >= now - timedelta(days=1),
        )))
        by_id = {row.id: row for row in actions}
        for revision in revisions:
            action = by_id.get(revision.action_id)
            if not action:
                continue
            if action.action_type == CREATE_INTERNAL_TASK and _aware(revision.created_at) >= now - timedelta(hours=1):
                quotas["task_hourly"]["used"] += 1
            if action.action_type == CREATE_INTERNAL_NOTIFICATION:
                if _aware(revision.created_at) >= now - timedelta(hours=1):
                    quotas["notification_project_hourly"]["used"] += 1
                try:
                    recipients[str(revision.envelope["payload"]["recipient_ref"]["id"]["value"])] += 1
                except (KeyError, TypeError):
                    pass
        quotas["notification_recipient_daily"]["max_used"] = max(recipients.values(), default=0)
    return {
        "actions": dict(states), "receipts": dict(outcomes), "jobs": dict(job_states),
        "incidents": incidents, "quotas": quotas,
    }


def project_autonomy_readiness(db, project, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    policy, rules, policy_history = _policy_for_project(db, project)
    if policy is None:
        return None
    settings, runtime_state = _runtime_projection(project.id)
    owner_id = getattr(settings, "owner_user_id", None)
    if owner_id is None:
        try:
            owner_id = int(rules["changed_by"]["id"]["value"])
        except (KeyError, TypeError, ValueError):
            owner_id = None
    authority = db.scalar(select(AuthorityState).where(
        AuthorityState.organization_id == project.organization_id,
        AuthorityState.project_id == project.id,
        AuthorityState.principal_kind == "user",
        AuthorityState.principal_id == str(owner_id),
        AuthorityState.scope == PILOT_SCOPE,
    )) if owner_id else None
    policy_until = _aware(policy.valid_until)
    authority_until = _aware(authority.valid_until) if authority else None
    authority_ready = bool(
        authority and authority.state == "active" and authority.membership_role == "owner"
        and authority_until > now and authority.authority_epoch == rules["authority_epoch"]
        and isinstance(authority.permissions, list)
        and PILOT_OPERATIONS.issubset(set(authority.permissions))
    )
    policy_ready = bool(rules["enabled"] and policy_until > now)
    mailbox = _mailbox(db, project)
    operations = _operations(db, project, policy, settings, now)

    blockers = []
    degraded = []
    if runtime_state == "invalid": blockers.append("runtime_configuration_invalid")
    elif runtime_state == "mismatch": blockers.append("runtime_project_scope_mismatch")
    elif runtime_state == "off": blockers.append("runtime_disabled")
    if settings and settings.enabled and settings.owner_user_id != owner_id:
        blockers.append("runtime_owner_scope_mismatch")
    if not policy_ready: blockers.append("policy_disabled_or_expired")
    if not authority_ready: blockers.append("authority_revoked_expired_or_mismatched")
    if not mailbox["ready"]: blockers.append("mailbox_cutover_not_ready")
    if operations["incidents"]: blockers.append("unresolved_incident")
    degraded.extend(exhausted_quota_reasons(operations["quotas"]))
    if runtime_state == "matching":
        degraded.append("runtime_component_alignment_unverified")
    status = "BLOCKED" if blockers else ("DEGRADED" if degraded else "ACTIVE")
    if runtime_state == "off" and len(blockers) == 1:
        status = "OFF"
    return {
        "project_id": project.id,
        "observed_at": now,
        "overall": {"status": status, "blockers": blockers, "warnings": degraded},
        "runtime": {
            "state": runtime_state,
            "pilot_enabled": bool(settings and settings.enabled),
            "producer_enabled": bool(settings and settings.producer_enabled),
            "notification_enabled": bool(settings and settings.notification_enabled),
            "scope_matches": runtime_state == "matching",
            "component_alignment": "unverified",
        },
        "policy": {
            "id": policy.id, "revision": policy.revision,
            "hash_prefix": policy.policy_hash[:12], "enabled": rules["enabled"],
            "task_mode": rules["action_modes"][CREATE_INTERNAL_TASK],
            "notification_mode": rules["action_modes"].get(CREATE_INTERNAL_NOTIFICATION, "CONFIRM"),
            "external_message_mode": rules["action_modes"][SEND_EXTERNAL_MESSAGE],
            "authority_epoch": rules["authority_epoch"],
            "changed_at": rules["changed_at"], "valid_until": policy_until,
            "ttl_seconds": _remaining(policy_until, now), "ready": policy_ready,
            "history": policy_history,
        },
        "authority": ({
            "state": authority.state, "membership_role": authority.membership_role,
            "authority_epoch": authority.authority_epoch, "record_version": authority.record_version,
            "valid_until": authority_until, "ttl_seconds": _remaining(authority_until, now),
            "ready": authority_ready,
        } if authority else {"state": "missing", "ready": False}),
        "mailbox": mailbox,
        "quotas": operations.pop("quotas"),
        "operations": operations,
    }
