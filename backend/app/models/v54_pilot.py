"""Additive synthetic pilot records. No provider I/O, activation or data backfill.

New UUIDs are local pilot identities; existing domain integer keys are unchanged.
Foreign keys prevent cross-tenant source/version bindings. Polymorphic refs and
legacy domain/project ownership additionally require the server Resolver contract.
"""
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, JSON, PrimaryKeyConstraint, String, UniqueConstraint, Uuid, event, inspect, select, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def new_id():
    return str(uuid4())


class Scoped:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))


def scoped_unique(name):
    return UniqueConstraint("organization_id", "id", name=name)


def scoped_fk(column, table, name):
    return ForeignKeyConstraint(["organization_id", column], [table + ".organization_id", table + ".id"],
                                name=name, ondelete="RESTRICT")


class ConnectionIdentity(Scoped, Base):
    __tablename__ = "v54_connection_identities"
    __table_args__ = (
        scoped_unique("uq_v54_identity_scope"),
        UniqueConstraint("organization_id", "provider", "account_key", name="uq_v54_identity_account"),
        CheckConstraint("binding_epoch > 0 AND record_version > 0", name="ck_v54_identity_version"),
        CheckConstraint("state IN ('unverified','verified','revoked')", name="ck_v54_identity_state"),
    )
    provider: Mapped[str] = mapped_column(String(50))
    account_key: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(20), server_default="unverified")
    binding_epoch: Mapped[int] = mapped_column(server_default="1")
    record_version: Mapped[int] = mapped_column(server_default="1")
    # A reference to the credential owner, never a secret or copied account master.
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("integration_credentials.id", ondelete="RESTRICT"))
    credential_generation: Mapped[int | None] = mapped_column()
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailConnection(Scoped, Base):
    __tablename__ = "v54_mail_connections"
    __table_args__ = (
        scoped_unique("uq_v54_mail_scope"),
        scoped_fk("identity_id", "v54_connection_identities", "fk_v54_mail_identity"),
        UniqueConstraint("identity_id", "namespace", name="uq_v54_mail_namespace"),
        CheckConstraint("record_version > 0", name="ck_v54_mail_version"),
        CheckConstraint("state IN ('blocked','active','revoked')", name="ck_v54_mail_state"),
    )
    identity_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    namespace: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(20), server_default="blocked")
    record_version: Mapped[int] = mapped_column(server_default="1")


class SourceReference(Scoped, Base):
    __tablename__ = "v54_sources"
    __table_args__ = (
        scoped_unique("uq_v54_source_scope"),
        scoped_fk("origin_project_id", "projects", "fk_v54_source_project"),
        scoped_fk("identity_id", "v54_connection_identities", "fk_v54_source_identity"),
        scoped_fk("parent_source_id", "v54_sources", "fk_v54_source_parent"),
        UniqueConstraint("identity_id", "namespace", "external_id", "incarnation", name="uq_v54_source_identity"),
        CheckConstraint("incarnation > 0 AND record_version > 0", name="ck_v54_source_version"),
        CheckConstraint("object_kind IN ('message','attachment','file','folder','record')", name="ck_v54_source_kind"),
        CheckConstraint("freshness IN ('unknown','fresh','stale')", name="ck_v54_source_freshness"),
    )
    origin_project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    identity_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    # Provider is resolved from immutable identity.provider, not duplicated here.
    parent_source_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    namespace: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(1000))
    external_id_kind: Mapped[str] = mapped_column(String(30))
    incarnation: Mapped[int] = mapped_column(server_default="1")
    object_kind: Mapped[str] = mapped_column(String(20))
    canonical_locator: Mapped[dict] = mapped_column(JSON)
    record_version: Mapped[int] = mapped_column(server_default="1")
    freshness: Mapped[str] = mapped_column(String(20), server_default="unknown")
    sync_state: Mapped[str] = mapped_column(String(20), server_default="discovered")
    availability: Mapped[str] = mapped_column(String(30), server_default="unknown")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Typed policy pins supplied by owner; absent means deny, not infinite retention.
    policy_pins: Mapped[dict | None] = mapped_column(JSON)
    residency: Mapped[dict | None] = mapped_column(JSON)


class SourceVersion(Scoped, Base):
    __tablename__ = "v54_source_versions"
    __table_args__ = (
        scoped_unique("uq_v54_source_version_scope"),
        UniqueConstraint("organization_id", "source_id", "id", name="uq_v54_source_observation"),
        scoped_fk("source_id", "v54_sources", "fk_v54_version_source"),
        UniqueConstraint("source_id", "observation_key", name="uq_v54_observation_key"),
        CheckConstraint("revision = 1", name="ck_v54_observation_immutable_revision"),
        CheckConstraint("consistency IN ('revision_bound','digest_observed','metadata_only','unknown')",
                        name="ck_v54_observation_consistency"),
    )
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    revision: Mapped[int] = mapped_column(server_default="1")
    observation_key: Mapped[str] = mapped_column(String(200))
    provider_revision: Mapped[str | None] = mapped_column(String(500))
    consistency: Mapped[str] = mapped_column(String(30), server_default="unknown")
    locator_at_observation: Mapped[dict] = mapped_column(JSON)
    integrity: Mapped[list] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    legacy_document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"))


class SourceCurrent(Base):
    """Pointer separate from immutable observations to avoid circular insert/FKs."""
    __tablename__ = "v54_source_current"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "source_id", "version_id"],
                             ["v54_source_versions.organization_id", "v54_source_versions.source_id",
                              "v54_source_versions.id"], ondelete="RESTRICT", name="fk_v54_current_observation"),
    )
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))


class Evidence(Scoped, Base):
    __tablename__ = "v54_evidence"
    __table_args__ = (
        scoped_unique("uq_v54_evidence_scope"),
        UniqueConstraint("organization_id", "id", "source_id", "source_version_id",
                         name="uq_v54_evidence_binding"),
        ForeignKeyConstraint(["organization_id", "source_id", "source_version_id"],
                             ["v54_source_versions.organization_id", "v54_source_versions.source_id",
                              "v54_source_versions.id"], ondelete="RESTRICT", name="fk_v54_evidence_observation"),
        CheckConstraint("revision = 1", name="ck_v54_evidence_revision"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_v54_evidence_confidence"),
    )
    source_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    source_version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    revision: Mapped[int] = mapped_column(server_default="1")
    locator: Mapped[dict] = mapped_column(JSON)
    extractor: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column()
    confidence_kind: Mapped[str] = mapped_column(String(20), server_default="unknown")
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Opaque descriptor only: no text/cache/blob storage in this foundation.
    representation_ref: Mapped[dict | None] = mapped_column(JSON)
    policy_pins: Mapped[dict | None] = mapped_column(JSON)


class EvidenceAssessment(Base):
    __tablename__ = "v54_evidence_assessments"
    __table_args__ = (
        scoped_fk("evidence_id", "v54_evidence", "fk_v54_assessment_evidence"),
        CheckConstraint("record_version > 0", name="ck_v54_assessment_version"),
        CheckConstraint("verification IN ('verified','unverified')", name="ck_v54_assessment_verification"),
        CheckConstraint("freshness IN ('unknown','fresh','stale')", name="ck_v54_assessment_freshness"),
        CheckConstraint("verification != 'verified' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
                        name="ck_v54_assessment_reviewer"),
    )
    evidence_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    record_version: Mapped[int] = mapped_column(server_default="1")
    verification: Mapped[str] = mapped_column(String(20), server_default="unverified")
    freshness: Mapped[str] = mapped_column(String(20), server_default="unknown")
    availability: Mapped[str] = mapped_column(String(30), server_default="unknown")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeadlineClaim(Scoped, Base):
    """ID is the stable claim anchor; immutable assertions use (id, revision)."""
    __tablename__ = "v54_deadline_claims"
    __table_args__ = (
        PrimaryKeyConstraint("id", "revision"),
        UniqueConstraint("organization_id", "id", "revision", name="uq_v54_claim_scope"),
        scoped_fk("message_id", "messages", "fk_v54_claim_message"),
        CheckConstraint("revision > 0 AND record_version > 0", name="ck_v54_claim_version"),
        CheckConstraint("verification IN ('unverified','confirmed','rejected')", name="ck_v54_claim_state"),
        CheckConstraint("verification != 'confirmed' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
                        name="ck_v54_claim_reviewer"),
    )
    revision: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"))
    due_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(100))
    evidence_pins: Mapped[list] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    verification: Mapped[str] = mapped_column(String(20), server_default="unverified")
    record_version: Mapped[int] = mapped_column(server_default="1")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContextRelation(Scoped, Base):
    __tablename__ = "v54_context_relations"
    __table_args__ = (
        scoped_unique("uq_v54_context_scope"),
        scoped_fk("message_id", "messages", "fk_v54_context_message"),
        scoped_fk("receipt_id", "v54_receipts", "fk_v54_context_receipt"),
        UniqueConstraint("organization_id", "lineage_id", "revision", name="uq_v54_context_lineage"),
        CheckConstraint("revision > 0 AND record_version > 0", name="ck_v54_context_version"),
        CheckConstraint("state IN ('hypothesis','confirmed','rejected','superseded')", name="ck_v54_context_state"),
        CheckConstraint("relation_type IN ('communication.project','communication.contract','communication.task','communication.draft')",
                        name="ck_v54_context_type"),
        CheckConstraint("state != 'confirmed' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
                        name="ck_v54_context_reviewer"),
        Index("uq_v54_context_primary", "organization_id", "message_id", "relation_type", unique=True,
              postgresql_where=text("state = 'confirmed' AND relation_type IN ('communication.project','communication.contract')"),
              sqlite_where=text("state = 'confirmed' AND relation_type IN ('communication.project','communication.contract')")),
    )
    lineage_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    revision: Mapped[int] = mapped_column()
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"))
    relation_type: Mapped[str] = mapped_column(String(50))
    target_ref: Mapped[dict] = mapped_column(JSON)
    scope_ref: Mapped[dict] = mapped_column(JSON)
    expected_target: Mapped[dict] = mapped_column(JSON)
    expected_context_version: Mapped[int] = mapped_column()
    evidence_pins: Mapped[list] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), server_default="hypothesis")
    applicability: Mapped[str] = mapped_column(String(30), server_default="legacy_unverified")
    record_version: Mapped[int] = mapped_column(server_default="1")
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), unique=True)


class ActionPolicy(Scoped, Base):
    __tablename__ = "v54_action_policies"
    __table_args__ = (
        PrimaryKeyConstraint("id", "revision"),
        UniqueConstraint("organization_id", "id", "revision", name="uq_v54_policy_scope"),
        CheckConstraint("revision > 0", name="ck_v54_policy_revision"),
        CheckConstraint("mode = 'CONFIRM'", name="ck_v54_policy_confirm_only"),
    )
    revision: Mapped[int] = mapped_column(primary_key=True)
    policy_hash: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(10))
    # No scope/default rule authorizes a real tenant.
    scope_ref: Mapped[dict] = mapped_column(JSON)
    rules: Mapped[dict] = mapped_column(JSON)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PilotAction(Scoped, Base):
    """Business identity/reservation, independent of transport jobs/retries."""
    __tablename__ = "v54_actions"
    __table_args__ = (
        scoped_unique("uq_v54_action_scope"),
        scoped_fk("message_id", "messages", "fk_v54_action_message"),
        scoped_fk("project_id", "projects", "fk_v54_action_project"),
        scoped_fk("compensates_action_id", "v54_actions", "fk_v54_action_compensation"),
        UniqueConstraint("organization_id", "message_id", "claim_id", "action_type", name="uq_v54_action_intent"),
        CheckConstraint("action_type IN ('task.internal.create','task.internal.cancel')", name="ck_v54_action_type"),
        CheckConstraint("record_version > 0 AND reservation_fence >= 0", name="ck_v54_action_version"),
        CheckConstraint("business_state IN ('AWAITING_POLICY','AWAITING_APPROVAL','READY','BLOCKED','CANCELLED','EXECUTING','SUCCEEDED','FAILED_NOT_APPLIED','UNKNOWN')",
                        name="ck_v54_action_state"),
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"))
    claim_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    action_type: Mapped[str] = mapped_column(String(40))
    business_state: Mapped[str] = mapped_column(String(30), server_default="AWAITING_POLICY")
    record_version: Mapped[int] = mapped_column(server_default="1")
    reservation_fence: Mapped[int] = mapped_column(server_default="0")
    current_revision: Mapped[int | None] = mapped_column()
    compensates_action_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class ActionRevision(Base):
    __tablename__ = "v54_action_revisions"
    __table_args__ = (
        scoped_fk("action_id", "v54_actions", "fk_v54_revision_action"),
        ForeignKeyConstraint(["organization_id", "claim_id", "claim_revision"],
                             ["v54_deadline_claims.organization_id", "v54_deadline_claims.id", "v54_deadline_claims.revision"],
                             ondelete="RESTRICT", name="fk_v54_revision_claim"),
        ForeignKeyConstraint(["organization_id", "policy_id", "policy_revision"],
                             ["v54_action_policies.organization_id", "v54_action_policies.id", "v54_action_policies.revision"],
                             ondelete="RESTRICT", name="fk_v54_revision_policy"),
        UniqueConstraint("organization_id", "action_id", "revision", "envelope_hash", name="uq_v54_sealed_revision"),
        UniqueConstraint("organization_id", "command_key", name="uq_v54_command_key"),
        CheckConstraint("revision > 0 AND length(envelope_hash) = 64", name="ck_v54_revision_hash"),
    )
    action_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    claim_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    claim_revision: Mapped[int] = mapped_column()
    policy_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    policy_revision: Mapped[int] = mapped_column()
    envelope: Mapped[dict] = mapped_column(JSON)
    envelope_hash: Mapped[str] = mapped_column(String(64))
    command_key: Mapped[str] = mapped_column(String(200))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionApproval(Scoped, Base):
    __tablename__ = "v54_approvals"
    __table_args__ = (
        scoped_unique("uq_v54_approval_scope"),
        ForeignKeyConstraint(["organization_id", "action_id", "revision", "envelope_hash"],
                             ["v54_action_revisions.organization_id", "v54_action_revisions.action_id",
                              "v54_action_revisions.revision", "v54_action_revisions.envelope_hash"],
                             ondelete="RESTRICT", name="fk_v54_approval_seal"),
        UniqueConstraint("organization_id", "command_key", name="uq_v54_approval_command"),
        UniqueConstraint("organization_id", "id", "action_id", "revision", "envelope_hash", name="uq_v54_approval_binding"),
        CheckConstraint("state IN ('GRANTED','REJECTED','REVOKED','EXPIRED','INVALIDATED')", name="ck_v54_approval_state"),
        CheckConstraint("expires_at > granted_at AND authority_epoch > 0", name="ck_v54_approval_expiry"),
    )
    action_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    revision: Mapped[int] = mapped_column()
    envelope_hash: Mapped[str] = mapped_column(String(64))
    command_key: Mapped[str] = mapped_column(String(200))
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    authority_epoch: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(String(20))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionReceipt(Scoped, Base):
    __tablename__ = "v54_receipts"
    __table_args__ = (
        scoped_unique("uq_v54_receipt_scope"),
        UniqueConstraint("organization_id", "action_id", name="uq_v54_single_business_receipt"),
        ForeignKeyConstraint(["organization_id", "approval_id", "action_id", "revision", "envelope_hash"],
                             ["v54_approvals.organization_id", "v54_approvals.id", "v54_approvals.action_id",
                              "v54_approvals.revision", "v54_approvals.envelope_hash"],
                             ondelete="RESTRICT", name="fk_v54_receipt_approval"),
        CheckConstraint("outcome IN ('APPLIED','NOT_APPLIED','UNKNOWN')", name="ck_v54_receipt_outcome"),
        CheckConstraint("fence > 0", name="ck_v54_receipt_fence"),
    )
    action_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    revision: Mapped[int] = mapped_column()
    envelope_hash: Mapped[str] = mapped_column(String(64))
    approval_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    job_id: Mapped[int] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"))
    fence: Mapped[int] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(20))
    target_ref: Mapped[dict | None] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PendingDispatch(Base):
    """T1 commit-before-enqueue recovery index; existing BackgroundJob is transport."""
    __tablename__ = "v54_pending_dispatch"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "approval_id", "action_id", "revision", "envelope_hash"],
                             ["v54_approvals.organization_id", "v54_approvals.id", "v54_approvals.action_id",
                              "v54_approvals.revision", "v54_approvals.envelope_hash"],
                             ondelete="RESTRICT", name="fk_v54_pending_approval"),
        Index("ix_v54_pending_dispatch", "pending", "organization_id"),
    )
    action_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column()
    envelope_hash: Mapped[str] = mapped_column(String(64))
    approval_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    pending: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"))


class AuditExtension(Scoped, Base):
    """1:1 extension of existing AuditLog, never a second independent journal."""
    __tablename__ = "v54_audit_extensions"
    __table_args__ = (
        scoped_unique("uq_v54_audit_scope"),
        scoped_fk("project_id", "projects", "fk_v54_audit_project"),
        scoped_fk("approval_id", "v54_approvals", "fk_v54_audit_approval"),
        scoped_fk("receipt_id", "v54_receipts", "fk_v54_audit_receipt"),
        UniqueConstraint("organization_id", "subject_type", "subject_id", "sequence", name="uq_v54_audit_sequence"),
        CheckConstraint("sequence > 0", name="ck_v54_audit_sequence"),
    )
    audit_log_id: Mapped[int] = mapped_column(ForeignKey("audit_logs.id", ondelete="RESTRICT"), unique=True)
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(40))
    sequence: Mapped[int] = mapped_column()
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    action_pin: Mapped[dict | None] = mapped_column(JSON)
    subject_pin: Mapped[dict | None] = mapped_column(JSON)
    approval_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    receipt_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"))
    relation_refs: Mapped[list] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(100))


# Protect immutable assertions through normal ORM writes. Bulk SQL is not an
# authorization path; restricted DB roles/retention writer remain a rollout gate.
def _immutable_fields(mapper, connection, target):
    mutable = {
        SourceReference: {"canonical_locator", "record_version", "freshness", "sync_state", "availability",
                          "last_seen_at", "last_checked_at", "next_check_at", "policy_pins", "residency"},
        Evidence: {"representation_ref"},
        EvidenceAssessment: {"record_version", "verification", "freshness", "availability",
                             "checked_at", "valid_until", "reviewed_by", "reviewed_at"},
        DeadlineClaim: {"verification", "record_version", "reviewed_by", "reviewed_at"},
        ContextRelation: {"state", "applicability", "record_version", "confirmed_by", "confirmed_at"},
        ActionApproval: {"state"},
        ConnectionIdentity: {"state", "binding_epoch", "record_version", "credential_id",
                             "credential_generation", "verified_at"},
        MailConnection: {"state", "record_version"},
    }.get(type(target), set())
    if isinstance(target, Evidence):
        history = inspect(target).attrs.representation_ref.history
        if history.has_changes() and (not history.deleted or history.deleted[0] is not None):
            raise ValueError("immutable_pilot_assertion")
    if any(attr.history.has_changes() and attr.key not in mutable for attr in inspect(target).attrs):
        raise ValueError("immutable_pilot_assertion")


for _model in (ConnectionIdentity, MailConnection, SourceReference, SourceVersion, Evidence, DeadlineClaim,
               ContextRelation, ActionPolicy, ActionRevision, ActionApproval, ActionReceipt, AuditExtension):
    event.listen(_model, "before_update", _immutable_fields)


def _validate_insert(mapper, connection, target):
    """Server structural checks. ACL and live-version checks remain resolver-owned."""
    from app.core.v54_dto import ActionEnvelope, canonical_hash
    from app.core.v54_refs import ObjectRef, TaggedId, VersionPin, require_same_tenant

    tenant = TaggedId(kind="int", value=str(target.organization_id))

    def validate_refs(value):
        if isinstance(value, dict):
            if "tenant_id" in value or "version_kind" in value:
                ref = (VersionPin.model_validate(value).ref if "version_kind" in value
                       else ObjectRef.model_validate(value))
                require_same_tenant(tenant, ref)
            else:
                for child in value.values():
                    validate_refs(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                validate_refs(child)

    for column in mapper.columns:
        if isinstance(column.type, JSON):
            validate_refs(getattr(target, column.key))
    if isinstance(target, SourceReference) and target.parent_source_id:
        parent = connection.execute(select(SourceReference.__table__).where(
            SourceReference.id == target.parent_source_id)).mappings().first()
        if (not parent or parent["organization_id"] != target.organization_id
                or parent["identity_id"] != target.identity_id or parent["namespace"] != target.namespace):
            raise ValueError("source_parent_scope_mismatch")
    if isinstance(target, (DeadlineClaim, ContextRelation)):
        pins = [VersionPin.model_validate(p) for p in target.evidence_pins]
        if not pins or any(p.ref.type != "evidence" or p.version_kind != "revision" for p in pins):
            raise ValueError("evidence_pins_required")
    if isinstance(target, ContextRelation):
        expected_type = {"communication.project": "project", "communication.contract": "contract",
                         "communication.task": "task", "communication.draft": "response_draft"}[target.relation_type]
        ref = ObjectRef.model_validate(target.target_ref)
        pin = VersionPin.model_validate(target.expected_target)
        if ref.type != expected_type or pin.ref != ref:
            raise ValueError("context_target_mismatch")
    if isinstance(target, ActionPolicy):
        if (canonical_hash(target.rules) != target.policy_hash
                or target.rules.get("synthetic_only") is not True
                or target.rules.get("auto_enabled") is not False
                or target.rules.get("external_execute") is not False):
            raise ValueError("synthetic_policy_binding_required")
    if isinstance(target, ActionRevision):
        seal = ActionEnvelope.model_validate(target.envelope)
        if (canonical_hash(target.envelope) != target.envelope_hash or seal.action_ref.id.value != target.action_id
                or seal.revision != target.revision or seal.claim.ref.id.value != target.claim_id
                or seal.claim.value != target.claim_revision or seal.policy.ref.id.value != target.policy_id
                or seal.policy.value != target.policy_revision or seal.requested_by.id.value != str(target.requested_by)
                or seal.idempotency_key != target.command_key):
            raise ValueError("seal_binding_mismatch")
        action = connection.execute(select(PilotAction.__table__).where(PilotAction.id == target.action_id)).mappings().first()
        policy = connection.execute(select(ActionPolicy.__table__).where(
            ActionPolicy.id == target.policy_id, ActionPolicy.revision == target.policy_revision)).mappings().first()
        if (not action or not policy or action["organization_id"] != target.organization_id
                or action["project_id"] != int(seal.project_ref.id.value)
                or action["claim_id"] != target.claim_id or action["action_type"] != seal.action_type
                or policy["policy_hash"] != seal.policy_sha256
                or action["business_state"] in {"EXECUTING", "UNKNOWN", "SUCCEEDED"}):
            raise ValueError("seal_binding_mismatch")


for _model in (ConnectionIdentity, MailConnection, SourceReference, SourceVersion, SourceCurrent,
               Evidence, EvidenceAssessment, DeadlineClaim, ContextRelation, ActionPolicy,
               PilotAction, ActionRevision, ActionApproval, ActionReceipt, PendingDispatch, AuditExtension):
    event.listen(_model, "before_insert", _validate_insert)


def _deny_ordinary_delete(mapper, connection, target):
    raise ValueError("pilot_retention_writer_required")


for _model in (ConnectionIdentity, MailConnection, SourceReference, SourceVersion, Evidence,
               DeadlineClaim, ContextRelation, ActionPolicy, PilotAction, ActionRevision,
               ActionApproval, ActionReceipt, AuditExtension):
    event.listen(_model, "before_delete", _deny_ordinary_delete)


def _validate_message_origin(mapper, connection, target):
    if target.mail_connection_id is None:
        return  # Legacy origin remains unresolved, not inferred from active project.
    mail = connection.execute(select(MailConnection.__table__).where(
        MailConnection.id == target.mail_connection_id)).mappings().first()
    source = connection.execute(select(SourceReference.__table__).where(
        SourceReference.id == target.source_reference_id)).mappings().first()
    if (not mail or not source or mail["organization_id"] != target.organization_id
            or source["organization_id"] != target.organization_id
            or source["identity_id"] != mail["identity_id"] or source["namespace"] != mail["namespace"]
            or source["object_kind"] != "message" or source["external_id"] != target.provider_message_id):
        raise ValueError("message_origin_scope_mismatch")


from app.models.ai_secretary import Message

event.listen(Message, "before_insert", _validate_message_origin)
event.listen(Message, "before_update", _validate_message_origin)
