"""Default-off product composition for the narrow MVP-5 internal-action pilot.

This module does not enable a policy, create authority rows, or produce actions.
It only makes explicitly authorized, already sealed internal intents executable
by the durable queue for one configured owner/project pair.  Each capability
has its own opt-in; notification AUTO is off even when the task pilot is on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.action_trust.facade import TrustFacade
from app.action_trust.guards import Guards, TrustConflict
from app.autonomy_policy import AutonomyConflict, AutonomyPolicyService
from app.context_communication.service import ContextCommunication
from app.core.v54_authority import AuthorityDenied, AuthorityResolver
from app.core.v54_dto import ActionEnvelope
from app.core.v54_interfaces import PilotGate, RequestScope, Resolution
from app.core.v54_permissions import utc
from app.core.v54_refs import VersionPin, require_same_tenant
from app.database import SessionLocal
from app.models.ai_secretary import Message
from app.models.mailbox_identity import MailboxCredentialGeneration
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_pilot import (
    ActionPolicy,
    ActionRevision,
    ConnectionIdentity,
    ContextRelation,
    DeadlineClaim,
    Evidence,
    EvidenceAssessment,
    MailConnection,
    PendingDispatch,
    PilotAction,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)
from app.pilot_dispatch import ProductDispatch, install_product_runtime
from app.pilot_notification_mutation import InternalMutationRouter, InternalNotificationMutation
from app.pilot_task_mutation import InternalTaskMutation


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _required_int(name: str) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        raise TrustConflict("product_scope_required") from None
    if value <= 0:
        raise TrustConflict("product_scope_required")
    return value


@dataclass(frozen=True, slots=True)
class ProductPilotSettings:
    enabled: bool
    project_id: int | None = None
    owner_user_id: int | None = None
    hourly_quota: int = 3
    policy_ttl_hours: int = 24
    minimum_confidence: float = 0.9
    notification_enabled: bool = False
    notification_ttl_hours: int = 6
    notification_project_hourly_quota: int = 3
    notification_recipient_daily_quota: int = 10

    def __post_init__(self):
        if not self.enabled:
            return
        if (type(self.project_id) is not int or self.project_id <= 0
                or type(self.owner_user_id) is not int or self.owner_user_id <= 0
                or type(self.hourly_quota) is not int or not 2 <= self.hourly_quota <= 3
                or type(self.policy_ttl_hours) is not int or not 24 <= self.policy_ttl_hours <= 48
                or type(self.minimum_confidence) is not float
                or not 0.9 <= self.minimum_confidence <= 1.0
                or type(self.notification_enabled) is not bool
                or type(self.notification_ttl_hours) is not int
                or not 1 <= self.notification_ttl_hours <= 6
                or type(self.notification_project_hourly_quota) is not int
                or not 1 <= self.notification_project_hourly_quota <= 3
                or type(self.notification_recipient_daily_quota) is not int
                or not 1 <= self.notification_recipient_daily_quota <= 10):
            raise TrustConflict("product_pilot_configuration_invalid")


def load_product_pilot_settings() -> ProductPilotSettings:
    enabled = _enabled(os.getenv("PU_V54_AUTO_PILOT_ENABLED"))
    if not enabled:
        return ProductPilotSettings(enabled=False)
    try:
        quota = int(os.getenv("PU_V54_AUTO_PILOT_HOURLY_QUOTA", "3"))
        ttl = int(os.getenv("PU_V54_AUTO_PILOT_POLICY_TTL_HOURS", "24"))
        confidence = float(os.getenv("PU_V54_AUTO_PILOT_MIN_CONFIDENCE", "0.9"))
        notification_ttl = int(os.getenv("PU_V54_AUTO_NOTIFICATION_TTL_HOURS", "6"))
        notification_hourly = int(os.getenv("PU_V54_AUTO_NOTIFICATION_PROJECT_HOURLY_QUOTA", "3"))
        notification_daily = int(os.getenv("PU_V54_AUTO_NOTIFICATION_RECIPIENT_DAILY_QUOTA", "10"))
    except ValueError:
        raise TrustConflict("product_pilot_configuration_invalid") from None
    return ProductPilotSettings(
        enabled=True,
        project_id=_required_int("PU_V54_AUTO_PILOT_PROJECT_ID"),
        owner_user_id=_required_int("PU_V54_AUTO_PILOT_OWNER_USER_ID"),
        hourly_quota=quota,
        policy_ttl_hours=ttl,
        minimum_confidence=confidence,
        notification_enabled=_enabled(os.getenv("PU_V54_AUTO_NOTIFICATION_ENABLED")),
        notification_ttl_hours=notification_ttl,
        notification_project_hourly_quota=notification_hourly,
        notification_recipient_daily_quota=notification_daily,
    )


class ProductPilotPolicy:
    """Exact owner/project authority boundary; never grants or provisions it."""

    def __init__(self, settings: ProductPilotSettings, *, authority: AuthorityResolver, clock=utcnow):
        if not settings.enabled:
            raise TrustConflict("pilot_disabled")
        self.settings, self.authority, self.clock = settings, authority, clock

    def require(self, db, scope: RequestScope, operation: str, now: datetime, *, lock=False):
        if (not db.in_transaction() or now.tzinfo is None
                or int(scope.project.id.value) != self.settings.project_id
                or int(scope.actor.id.value) != self.settings.owner_user_id):
            raise TrustConflict("resource_unavailable")
        try:
            snapshot = self.authority.require(db, scope, operation, now, lock=lock)
        except AuthorityDenied:
            raise TrustConflict("resource_unavailable") from None
        if snapshot.membership_role != "owner":
            raise TrustConflict("resource_unavailable")
        return min(snapshot.valid_until, now + timedelta(minutes=5))

    def resolved_authority_epoch(self, db, scope, operation, now, *, lock=False):
        try:
            return self.authority.require(db, scope, operation, now, lock=lock).authority_epoch
        except AuthorityDenied:
            raise TrustConflict("resource_unavailable") from None

    def permits(self, db, scope, actor_id, operation, now, *, lock=False):
        if actor_id != self.settings.owner_user_id:
            raise TrustConflict("resource_unavailable")
        self.require(db, scope, operation, now, lock=lock)
        return True

    def identity(self, row, namespace=None):
        if (row is None or row.provider != "google_workspace" or row.state != "verified"
                or type(row.credential_generation) is not int
                or row.credential_generation <= 0 or row.verified_at is None
                or (namespace is not None and namespace != "gmail")):
            raise TrustConflict("resource_unavailable")


class ProductResolver:
    """Resolve current product records without synthetic provider assumptions."""

    def __init__(self, policy: ProductPilotPolicy, *, clock=utcnow):
        self.policy, self.clock = policy, clock

    @staticmethod
    def _load(db, model, *conditions, lock=False):
        query = select(model).where(*conditions).execution_options(populate_existing=True)
        return db.scalar(query.with_for_update() if lock else query)

    def _credential_generation(self, db, scope, identity, *, lock):
        self.policy.identity(identity, "gmail")
        generation = self._load(
            db,
            MailboxCredentialGeneration,
            MailboxCredentialGeneration.organization_id == int(scope.tenant.value),
            MailboxCredentialGeneration.connection_identity_id == identity.id,
            MailboxCredentialGeneration.generation == identity.credential_generation,
            MailboxCredentialGeneration.binding_epoch == identity.binding_epoch,
            MailboxCredentialGeneration.state == "active",
            lock=lock,
        )
        if generation is None or generation.verified_at is None:
            raise TrustConflict("resource_unavailable")
        return generation

    def _source_chain(self, db, scope, pin, *, lock):
        evidence = assessment = None
        if pin.ref.type == "evidence":
            evidence = self._load(db, Evidence, Evidence.id == pin.ref.id.value,
                                  Evidence.organization_id == int(scope.tenant.value), lock=lock)
            if evidence is None or evidence.revision != pin.value:
                raise TrustConflict("resource_unavailable")
            source_id, version_id = evidence.source_id, evidence.source_version_id
            assessment = self._load(db, EvidenceAssessment,
                                    EvidenceAssessment.evidence_id == evidence.id,
                                    EvidenceAssessment.organization_id == int(scope.tenant.value), lock=lock)
        elif pin.ref.type == "source_version":
            version_id = pin.ref.id.value
            version = self._load(db, SourceVersion, SourceVersion.id == version_id,
                                 SourceVersion.organization_id == int(scope.tenant.value), lock=lock)
            if version is None or version.revision != pin.value:
                raise TrustConflict("resource_unavailable")
            source_id = version.source_id
        elif pin.ref.type == "source":
            source_id, version_id = pin.ref.id.value, None
        else:
            raise TrustConflict("resource_unavailable")
        source = self._load(db, SourceReference, SourceReference.id == source_id,
                            SourceReference.organization_id == int(scope.tenant.value), lock=lock)
        current = self._load(db, SourceCurrent, SourceCurrent.source_id == source_id,
                             SourceCurrent.organization_id == int(scope.tenant.value), lock=lock)
        version = self._load(db, SourceVersion, SourceVersion.id == (version_id or (current.version_id if current else None)),
                             SourceVersion.organization_id == int(scope.tenant.value), lock=lock)
        identity = self._load(db, ConnectionIdentity,
                              ConnectionIdentity.id == (source.identity_id if source else None),
                              ConnectionIdentity.organization_id == int(scope.tenant.value), lock=lock)
        if (source is None or current is None or version is None or identity is None
                or source.origin_project_id != int(scope.project.id.value)
                or version.source_id != source.id or current.version_id != version.id
                or source.freshness != "fresh" or source.availability != "available"
                or source.sync_state != "current" or not isinstance(source.policy_pins, dict)
                or utc(source.next_check_at) is None or utc(source.next_check_at) <= self.clock()
                or set(source.policy_pins) != {"access", "retention", "residency"}
                or not isinstance(source.residency, dict) or not source.residency
                or version.consistency not in {"revision_bound", "digest_observed"}):
            raise TrustConflict("resource_unavailable")
        if source.namespace != "gmail":
            raise TrustConflict("resource_unavailable")
        self._credential_generation(db, scope, identity, lock=lock)
        return source, version, current, identity, evidence, assessment

    def _verbatim(self, db, source, evidence) -> bool:
        locator = evidence.locator if evidence and isinstance(evidence.locator, dict) else {}
        if locator.get("kind") != "text_range" or locator.get("unit") != "unicode_codepoint":
            return False
        start, end = locator.get("start"), locator.get("end")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            return False
        message = db.scalar(select(Message).where(Message.source_reference_id == source.id))
        return bool(message and end <= len(message.content or "") and (message.content or "")[start:end].strip())

    def resolve(self, db, *, scope: RequestScope, pin: VersionPin, operation: str, lock: bool) -> Resolution:
        now = self.clock()
        if operation not in {"metadata", "review", "dispatch"} or now.tzinfo is None:
            raise TrustConflict("resource_unavailable")
        require_same_tenant(scope.tenant, pin.ref)
        deadline = self.policy.require(db, scope, operation, now, lock=lock)
        version = None
        verified = "unverified"
        binding_epoch = self.policy.resolved_authority_epoch(db, scope, operation, now, lock=lock)
        kind, key = pin.ref.type, pin.ref.id.value
        if kind in {"source", "source_version", "evidence"}:
            source, observation, _current, identity, evidence, assessment = self._source_chain(db, scope, pin, lock=lock)
            binding_epoch = identity.binding_epoch
            deadline = min(deadline, utc(source.next_check_at) or deadline)
            if kind == "source":
                version = source.record_version
            elif kind == "source_version":
                version = observation.revision
            else:
                version = evidence.revision
                if (assessment is None or assessment.verification != "verified"
                        or assessment.freshness != "fresh" or assessment.availability != "available"
                        or utc(assessment.valid_until) is None
                        or utc(assessment.valid_until) <= now
                        or evidence.confidence is None
                        or evidence.confidence < self.policy.settings.minimum_confidence
                        or not self._verbatim(db, source, evidence)):
                    raise TrustConflict("resource_unavailable")
                deadline = min(deadline, utc(assessment.valid_until))
                verified = "verified"
        elif kind == "project":
            row = self._load(db, Project, Project.id == int(key), Project.archived_at.is_(None), lock=lock)
            version = row.record_version if row and row.organization_id == int(scope.tenant.value) else None
        elif kind == "contract":
            row = self._load(db, Contract, Contract.id == int(key), Contract.project_id == int(scope.project.id.value), lock=lock)
            version = row.record_version if row else None
        elif kind == "connection_identity":
            row = self._load(db, ConnectionIdentity, ConnectionIdentity.id == key,
                             ConnectionIdentity.organization_id == int(scope.tenant.value), lock=lock)
            self._credential_generation(db, scope, row, lock=lock)
            binding_epoch, version = row.binding_epoch, row.record_version
        elif kind == "mail_connection":
            row = self._load(db, MailConnection, MailConnection.id == key,
                             MailConnection.organization_id == int(scope.tenant.value), lock=lock)
            identity = self._load(
                db, ConnectionIdentity,
                ConnectionIdentity.id == (row.identity_id if row else None),
                ConnectionIdentity.organization_id == int(scope.tenant.value),
                lock=lock,
            )
            if row is None or row.state != "active" or row.namespace != "gmail":
                raise TrustConflict("resource_unavailable")
            self._credential_generation(db, scope, identity, lock=lock)
            binding_epoch, version = identity.binding_epoch, row.record_version
        elif kind == "message":
            row = self._load(db, Message, Message.id == int(key), Message.organization_id == int(scope.tenant.value),
                             Message.project_id == int(scope.project.id.value), lock=lock)
            if row is None or row.source_type != "email" or not row.source_reference_id or not row.mail_connection_id:
                raise TrustConflict("resource_unavailable")
            version = row.context_version
        elif kind == "deadline_claim":
            row = self._load(db, DeadlineClaim, DeadlineClaim.id == key,
                             DeadlineClaim.organization_id == int(scope.tenant.value),
                             DeadlineClaim.revision == pin.value, lock=lock)
            latest = db.scalar(select(func.max(DeadlineClaim.revision)).where(DeadlineClaim.id == key))
            version = row.revision if row and latest == row.revision else None
            verified = "verified" if row and row.verification == "confirmed" else "unverified"
        elif kind == "context_relation":
            row = self._load(db, ContextRelation, ContextRelation.id == key,
                             ContextRelation.organization_id == int(scope.tenant.value), lock=lock)
            version = row.revision if row and row.state == "confirmed" and row.applicability == "current" else None
        elif kind == "action":
            row = self._load(db, ActionRevision, ActionRevision.action_id == key,
                             ActionRevision.organization_id == int(scope.tenant.value),
                             ActionRevision.revision == pin.value, lock=lock)
            action = self._load(db, PilotAction, PilotAction.id == key,
                                PilotAction.organization_id == int(scope.tenant.value), lock=lock)
            version = row.revision if row and action and action.current_revision == row.revision else None
        elif kind == "task":
            row = self._load(db, Task, Task.id == int(key), Task.project_id == int(scope.project.id.value), lock=lock)
            version = row.record_version if row else None
        elif kind == "policy":
            row = self._load(db, ActionPolicy, ActionPolicy.id == key,
                             ActionPolicy.organization_id == int(scope.tenant.value),
                             ActionPolicy.revision == pin.value, lock=lock)
            version = row.revision if row else None
            if row is not None:
                deadline = min(deadline, utc(row.valid_until))
        else:
            raise TrustConflict("resource_unavailable")
        if version != pin.value:
            raise TrustConflict("resource_unavailable")
        return Resolution(
            pin=pin, actor=scope.actor, project=scope.project, operation=operation,
            acl="allow", version="current", freshness="fresh", availability="available",
            verification=verified, policy_known=True, retention_known=True,
            residency_allowed=True, valid_until=deadline,
            authority_epoch=self.policy.resolved_authority_epoch(db, scope, operation, now, lock=lock),
            binding_epoch=binding_epoch,
        )


class ProductTrustFacade(TrustFacade):
    """Bind AUTO eligibility to the exact evidence sealed in the envelope."""

    def _candidate(self, db, scope, row, envelope):
        candidate = super()._candidate(db, scope, row, envelope)
        evidence_rows = [db.get(Evidence, pin.ref.id.value) for pin in envelope.evidence]
        if any(item is None for item in evidence_rows):
            return candidate
        confidence = min((item.confidence for item in evidence_rows if item.confidence is not None), default=None)
        confidence_basis_points = round(confidence * 10_000) if confidence is not None else None
        verbatim = confidence_basis_points is not None and all(
            (source := db.get(SourceReference, item.source_id)) is not None
            and self.guards.resolver._verbatim(db, source, item)
            for item in evidence_rows
        )
        return candidate.model_copy(update={
            "confidence_basis_points": confidence_basis_points,
            "verbatim_evidence": verbatim,
        })


class ProductAutonomyPolicyService(AutonomyPolicyService):
    """Apply the owner-approved TTL, evidence threshold and atomic hourly quota."""

    def __init__(self, *, settings: ProductPilotSettings, authority: AuthorityResolver, clock=utcnow):
        super().__init__(authority=authority, clock=clock)
        self.settings = settings

    def _limited(self, db, scope, candidate, decision, *, enforce_quota):
        if decision.mode != "AUTO":
            return decision
        notification = candidate.action_type == "notification.internal.create"
        if notification and not self.settings.notification_enabled:
            return decision.model_copy(update={
                "mode": "CONFIRM",
                "reason": "notification_auto_disabled",
            })
        if (candidate.confidence_basis_points is None
                or candidate.confidence_basis_points < round(self.settings.minimum_confidence * 10_000)
                or candidate.verbatim_evidence is not True):
            return decision.model_copy(update={
                "mode": "CONFIRM",
                "reason": "verified_verbatim_evidence_required",
            })
        current, view = self.lock_current_view(db, scope=scope)
        if (current is None or view is None
                or view.valid_until - view.changed_at > timedelta(hours=self.settings.policy_ttl_hours)):
            return decision.model_copy(update={"mode": "CONFIRM", "reason": "policy_ttl_exceeds_pilot_limit"})
        if enforce_quota:
            if notification:
                quota = self._notification_quota(db, scope, candidate, current)
                if quota is not None:
                    return decision.model_copy(update={"mode": "CONFIRM", "reason": quota})
            else:
                used = db.scalar(
                    select(func.count())
                    .select_from(PendingDispatch)
                    .join(ActionRevision, ActionRevision.action_id == PendingDispatch.action_id)
                    .join(PilotAction, PilotAction.id == PendingDispatch.action_id)
                    .where(
                        PendingDispatch.authorization_origin == "SERVER_POLICY",
                        PendingDispatch.policy_id == current.id,
                        PilotAction.project_id == self.settings.project_id,
                        ActionRevision.revision == PendingDispatch.revision,
                        ActionRevision.created_at >= self._now() - timedelta(hours=1),
                    )
                ) or 0
                if used >= self.settings.hourly_quota:
                    return decision.model_copy(update={"mode": "CONFIRM", "reason": "hourly_quota_exhausted"})
        if notification:
            return decision.model_copy(update={
                "valid_until": min(
                    decision.valid_until,
                    self._now() + timedelta(hours=self.settings.notification_ttl_hours),
                ),
            })
        return decision

    def _notification_quota(self, db, scope, candidate, current):
        """Count sealed AUTO reservations while the current policy row is locked.

        ``lock_current_view`` above serializes this calculation for the one
        configured project.  Counting dispatch reservations (including completed
        ones) keeps crash/retry paths from obtaining fresh quota.
        """
        candidate_row = db.scalar(select(ActionRevision).where(
            ActionRevision.organization_id == int(scope.tenant.value),
            ActionRevision.envelope_hash == candidate.envelope_sha256,
        ))
        if candidate_row is None:
            return "notification_binding_unavailable"
        try:
            candidate_envelope = ActionEnvelope.model_validate(candidate_row.envelope)
            recipient_id = int(candidate_envelope.payload.recipient_ref.id.value)
        except (AttributeError, TypeError, ValueError):
            return "notification_binding_unavailable"
        now = self._now()
        rows = db.execute(
            select(ActionRevision.envelope, ActionRevision.created_at)
            .select_from(PendingDispatch)
            .join(ActionRevision, ActionRevision.action_id == PendingDispatch.action_id)
            .join(PilotAction, PilotAction.id == PendingDispatch.action_id)
            .where(
                PendingDispatch.authorization_origin == "SERVER_POLICY",
                PendingDispatch.policy_id == current.id,
                PilotAction.project_id == self.settings.project_id,
                PilotAction.action_type == "notification.internal.create",
                ActionRevision.revision == PendingDispatch.revision,
                ActionRevision.created_at >= now - timedelta(days=1),
            )
        ).all()
        project_used = 0
        recipient_used = 0
        for sealed, created_at in rows:
            created_at = utc(created_at)
            if created_at >= now - timedelta(hours=1):
                project_used += 1
            try:
                existing = ActionEnvelope.model_validate(sealed)
                if int(existing.payload.recipient_ref.id.value) == recipient_id:
                    recipient_used += 1
            except (AttributeError, TypeError, ValueError):
                return "notification_binding_unavailable"
        if project_used >= self.settings.notification_project_hourly_quota:
            return "notification_project_hourly_quota_exhausted"
        if recipient_used >= self.settings.notification_recipient_daily_quota:
            return "notification_recipient_daily_quota_exhausted"
        return None

    def decide(self, db, *, scope, candidate):
        decision = super().decide(db, scope=scope, candidate=candidate)
        return self._limited(db, scope, candidate, decision, enforce_quota=True)

    def recheck(self, db, *, scope, candidate, decision):
        exact = (
            decision.action_type == candidate.action_type,
            decision.stage == candidate.stage,
            decision.risk == candidate.risk,
            decision.reversal == candidate.reversal,
            decision.effects == candidate.effects,
            decision.envelope_sha256 == candidate.envelope_sha256,
            decision.payload_sha256 == candidate.payload_sha256,
            decision.confidence_basis_points == candidate.confidence_basis_points,
            decision.verbatim_evidence == candidate.verbatim_evidence,
        )
        if not all(exact) or decision.valid_until <= self._now():
            raise AutonomyConflict("stale_action_binding")
        live = self._limited(
            db, scope, candidate,
            AutonomyPolicyService.decide(self, db, scope=scope, candidate=candidate),
            enforce_quota=False,
        )
        if (live.mode != decision.mode or live.policy != decision.policy
                or live.policy_sha256 != decision.policy_sha256
                or live.policy_authority_epoch != decision.policy_authority_epoch):
            raise AutonomyConflict("stale_policy_decision")
        return live


class ProductPilotComposition:
    def __init__(self, *, settings: ProductPilotSettings, clock=utcnow):
        authority = AuthorityResolver(clock=clock)
        self.policy = ProductPilotPolicy(settings, authority=authority, clock=clock)
        self.clock, self.enabled = clock, True
        self.resolver = ProductResolver(self.policy, clock=clock)
        self.guards = Guards(resolver=self.resolver, authorize=self.authorize, gate=self.gate, clock=clock)
        self.autonomy = ProductAutonomyPolicyService(settings=settings, authority=authority, clock=clock)
        self.trust = ProductTrustFacade(guards=self.guards, autonomy=self.autonomy)
        task_mutation = InternalTaskMutation(
            guards=self.guards, trust=self.trust, source_type="v54_auto",
            source_file_name="MVP-5 owner AUTO action",
        )
        self.mutation = InternalMutationRouter(
            task=task_mutation,
            notification=InternalNotificationMutation(
                guards=self.guards,
                trust=self.trust,
                project_hourly_quota=settings.notification_project_hourly_quota,
                recipient_daily_quota=settings.notification_recipient_daily_quota,
            ),
        )

    def authorize(self, db, scope, operation, subject, *, lock):
        require_same_tenant(scope.tenant, subject)
        self.policy.require(db, scope, operation, self.clock(), lock=True)
        if operation == "task.assign":
            if subject.type != "user":
                raise TrustConflict("resource_unavailable")
            member = db.scalar(select(ProjectMember.id).where(
                ProjectMember.project_id == self.policy.settings.project_id,
                ProjectMember.user_id == int(subject.id.value),
            ))
            if member is None:
                raise TrustConflict("resource_unavailable")
        return True

    def gate(self, db, scope):
        valid_until = self.policy.require(db, scope, "action.execute", self.clock(), lock=True)
        return PilotGate(
            product_scope_authorized=True,
            roles_known=True,
            retention_known=True,
            valid_until=valid_until,
        )

    def context(self, db, scope):
        return ContextCommunication(
            resolver=self.resolver,
            gate=self.gate(db, scope),
            authorize_audit=self.guards.audit_allowed,
            identity_providers=frozenset({"google_workspace"}),
            message_source_types=frozenset({"email"}),
            clock=self.clock,
        )


def install_product_pilot_runtime(*, settings: ProductPilotSettings | None = None,
                                  allow_sqlite_for_tests: bool = False) -> bool:
    settings = settings or load_product_pilot_settings()
    if not settings.enabled:
        return False
    runtime = ProductDispatch(
        sessions=SessionLocal,
        composition_for_scope=lambda _scope: ProductPilotComposition(settings=settings),
        project_id=settings.project_id,
        owner_user_id=settings.owner_user_id,
        notification_enabled=settings.notification_enabled,
        allow_sqlite_for_tests=allow_sqlite_for_tests,
    )
    install_product_runtime(runtime)
    return True
