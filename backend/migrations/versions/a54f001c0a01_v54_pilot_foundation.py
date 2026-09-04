"""Add inactive v5.4 synthetic CONFIRM foundation.

Revision ID: a54f001c0a01
Revises: f360a1b2c3d4
No backfill, credentials, provider I/O or policy seed.
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a01"
down_revision = "f360a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint('uq_v54_project_scope', 'projects', ['organization_id', 'id'])
    op.create_unique_constraint('uq_v54_message_scope', 'messages', ['organization_id', 'id'])
    op.create_table('v54_action_policies',
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('policy_hash', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('scope_ref', sa.JSON(), nullable=False),
    sa.Column('rules', sa.JSON(), nullable=False),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("mode = 'CONFIRM'", name='ck_v54_policy_confirm_only'),
    sa.CheckConstraint('revision > 0', name='ck_v54_policy_revision'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', 'revision'),
    sa.UniqueConstraint('organization_id', 'id', 'revision', name='uq_v54_policy_scope')
    )
    op.create_table('v54_connection_identities',
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('account_key', sa.String(length=255), nullable=False),
    sa.Column('state', sa.String(length=20), server_default='unverified', nullable=False),
    sa.Column('binding_epoch', sa.Integer(), server_default='1', nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('credential_id', sa.Integer(), nullable=True),
    sa.Column('credential_generation', sa.Integer(), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("state IN ('unverified','verified','revoked')", name='ck_v54_identity_state'),
    sa.CheckConstraint('binding_epoch > 0 AND record_version > 0', name='ck_v54_identity_version'),
    sa.ForeignKeyConstraint(['credential_id'], ['integration_credentials.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_identity_scope'),
    sa.UniqueConstraint('organization_id', 'provider', 'account_key', name='uq_v54_identity_account')
    )
    op.create_table('v54_mail_connections',
    sa.Column('identity_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('namespace', sa.String(length=255), nullable=False),
    sa.Column('state', sa.String(length=20), server_default='blocked', nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("state IN ('blocked','active','revoked')", name='ck_v54_mail_state'),
    sa.CheckConstraint('record_version > 0', name='ck_v54_mail_version'),
    sa.ForeignKeyConstraint(['organization_id', 'identity_id'], ['v54_connection_identities.organization_id', 'v54_connection_identities.id'], name='fk_v54_mail_identity', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identity_id', 'namespace', name='uq_v54_mail_namespace'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_mail_scope')
    )
    op.create_table('v54_sources',
    sa.Column('origin_project_id', sa.Integer(), nullable=False),
    sa.Column('identity_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('parent_source_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('namespace', sa.String(length=255), nullable=False),
    sa.Column('external_id', sa.String(length=1000), nullable=False),
    sa.Column('external_id_kind', sa.String(length=30), nullable=False),
    sa.Column('incarnation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('object_kind', sa.String(length=20), nullable=False),
    sa.Column('canonical_locator', sa.JSON(), nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('freshness', sa.String(length=20), server_default='unknown', nullable=False),
    sa.Column('sync_state', sa.String(length=20), server_default='discovered', nullable=False),
    sa.Column('availability', sa.String(length=30), server_default='unknown', nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('policy_pins', sa.JSON(), nullable=True),
    sa.Column('residency', sa.JSON(), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("freshness IN ('unknown','fresh','stale')", name='ck_v54_source_freshness'),
    sa.CheckConstraint("object_kind IN ('message','attachment','file','folder','record')", name='ck_v54_source_kind'),
    sa.CheckConstraint('incarnation > 0 AND record_version > 0', name='ck_v54_source_version'),
    sa.ForeignKeyConstraint(['organization_id', 'identity_id'], ['v54_connection_identities.organization_id', 'v54_connection_identities.id'], name='fk_v54_source_identity', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'origin_project_id'], ['projects.organization_id', 'projects.id'], name='fk_v54_source_project', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'parent_source_id'], ['v54_sources.organization_id', 'v54_sources.id'], name='fk_v54_source_parent', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['origin_project_id'], ['projects.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identity_id', 'namespace', 'external_id', 'incarnation', name='uq_v54_source_identity'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_source_scope')
    )
    op.create_table('v54_source_versions',
    sa.Column('source_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('observation_key', sa.String(length=200), nullable=False),
    sa.Column('provider_revision', sa.String(length=500), nullable=True),
    sa.Column('consistency', sa.String(length=30), server_default='unknown', nullable=False),
    sa.Column('locator_at_observation', sa.JSON(), nullable=False),
    sa.Column('integrity', sa.JSON(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('legacy_document_version_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("consistency IN ('revision_bound','digest_observed','metadata_only','unknown')", name='ck_v54_observation_consistency'),
    sa.CheckConstraint('revision = 1', name='ck_v54_observation_immutable_revision'),
    sa.ForeignKeyConstraint(['legacy_document_version_id'], ['document_versions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'source_id'], ['v54_sources.organization_id', 'v54_sources.id'], name='fk_v54_version_source', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_source_version_scope'),
    sa.UniqueConstraint('organization_id', 'source_id', 'id', name='uq_v54_source_observation'),
    sa.UniqueConstraint('source_id', 'observation_key', name='uq_v54_observation_key')
    )
    op.create_table('v54_actions',
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('action_type', sa.String(length=40), nullable=False),
    sa.Column('business_state', sa.String(length=30), server_default='AWAITING_POLICY', nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('reservation_fence', sa.Integer(), server_default='0', nullable=False),
    sa.Column('current_revision', sa.Integer(), nullable=True),
    sa.Column('compensates_action_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("action_type IN ('task.internal.create','task.internal.cancel')", name='ck_v54_action_type'),
    sa.CheckConstraint("business_state IN ('AWAITING_POLICY','AWAITING_APPROVAL','READY','BLOCKED','CANCELLED','EXECUTING','SUCCEEDED','FAILED_NOT_APPLIED','UNKNOWN')", name='ck_v54_action_state'),
    sa.CheckConstraint('record_version > 0 AND reservation_fence >= 0', name='ck_v54_action_version'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'compensates_action_id'], ['v54_actions.organization_id', 'v54_actions.id'], name='fk_v54_action_compensation', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'message_id'], ['messages.organization_id', 'messages.id'], name='fk_v54_action_message', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], name='fk_v54_action_project', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_action_scope'),
    sa.UniqueConstraint('organization_id', 'message_id', 'claim_id', 'action_type', name='uq_v54_action_intent')
    )
    op.create_table('v54_deadline_claims',
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('timezone', sa.String(length=100), nullable=False),
    sa.Column('evidence_pins', sa.JSON(), nullable=False),
    sa.Column('provenance', sa.JSON(), nullable=False),
    sa.Column('verification', sa.String(length=20), server_default='unverified', nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("verification != 'confirmed' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name='ck_v54_claim_reviewer'),
    sa.CheckConstraint("verification IN ('unverified','confirmed','rejected')", name='ck_v54_claim_state'),
    sa.CheckConstraint('revision > 0 AND record_version > 0', name='ck_v54_claim_version'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'message_id'], ['messages.organization_id', 'messages.id'], name='fk_v54_claim_message', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', 'revision'),
    sa.UniqueConstraint('organization_id', 'id', 'revision', name='uq_v54_claim_scope')
    )
    op.create_table('v54_evidence',
    sa.Column('source_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('source_version_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('locator', sa.JSON(), nullable=False),
    sa.Column('extractor', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('confidence_kind', sa.String(length=20), server_default='unknown', nullable=False),
    sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('representation_ref', sa.JSON(), nullable=True),
    sa.Column('policy_pins', sa.JSON(), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name='ck_v54_evidence_confidence'),
    sa.CheckConstraint('revision = 1', name='ck_v54_evidence_revision'),
    sa.ForeignKeyConstraint(['organization_id', 'source_id', 'source_version_id'], ['v54_source_versions.organization_id', 'v54_source_versions.source_id', 'v54_source_versions.id'], name='fk_v54_evidence_observation', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_evidence_scope')
    )
    op.create_table('v54_source_current',
    sa.Column('source_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('version_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'source_id', 'version_id'], ['v54_source_versions.organization_id', 'v54_source_versions.source_id', 'v54_source_versions.id'], name='fk_v54_current_observation', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('source_id')
    )
    op.create_table('v54_action_revisions',
    sa.Column('action_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('claim_revision', sa.Integer(), nullable=False),
    sa.Column('policy_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('policy_revision', sa.Integer(), nullable=False),
    sa.Column('envelope', sa.JSON(), nullable=False),
    sa.Column('envelope_hash', sa.String(length=64), nullable=False),
    sa.Column('command_key', sa.String(length=200), nullable=False),
    sa.Column('requested_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('revision > 0 AND length(envelope_hash) = 64', name='ck_v54_revision_hash'),
    sa.ForeignKeyConstraint(['organization_id', 'action_id'], ['v54_actions.organization_id', 'v54_actions.id'], name='fk_v54_revision_action', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'claim_id', 'claim_revision'], ['v54_deadline_claims.organization_id', 'v54_deadline_claims.id', 'v54_deadline_claims.revision'], name='fk_v54_revision_claim', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'policy_id', 'policy_revision'], ['v54_action_policies.organization_id', 'v54_action_policies.id', 'v54_action_policies.revision'], name='fk_v54_revision_policy', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('action_id', 'revision'),
    sa.UniqueConstraint('organization_id', 'action_id', 'revision', 'envelope_hash', name='uq_v54_sealed_revision'),
    sa.UniqueConstraint('organization_id', 'command_key', name='uq_v54_command_key')
    )
    op.create_table('v54_evidence_assessments',
    sa.Column('evidence_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('verification', sa.String(length=20), server_default='unverified', nullable=False),
    sa.Column('freshness', sa.String(length=20), server_default='unknown', nullable=False),
    sa.Column('availability', sa.String(length=30), server_default='unknown', nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("freshness IN ('unknown','fresh','stale')", name='ck_v54_assessment_freshness'),
    sa.CheckConstraint("verification != 'verified' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name='ck_v54_assessment_reviewer'),
    sa.CheckConstraint("verification IN ('verified','unverified')", name='ck_v54_assessment_verification'),
    sa.CheckConstraint('record_version > 0', name='ck_v54_assessment_version'),
    sa.ForeignKeyConstraint(['organization_id', 'evidence_id'], ['v54_evidence.organization_id', 'v54_evidence.id'], name='fk_v54_assessment_evidence', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('evidence_id')
    )
    op.create_table('v54_approvals',
    sa.Column('action_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('envelope_hash', sa.String(length=64), nullable=False),
    sa.Column('command_key', sa.String(length=200), nullable=False),
    sa.Column('approver_id', sa.Integer(), nullable=False),
    sa.Column('authority_epoch', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("state IN ('GRANTED','REJECTED','REVOKED','EXPIRED','INVALIDATED')", name='ck_v54_approval_state'),
    sa.CheckConstraint('expires_at > granted_at AND authority_epoch > 0', name='ck_v54_approval_expiry'),
    sa.ForeignKeyConstraint(['approver_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'action_id', 'revision', 'envelope_hash'], ['v54_action_revisions.organization_id', 'v54_action_revisions.action_id', 'v54_action_revisions.revision', 'v54_action_revisions.envelope_hash'], name='fk_v54_approval_seal', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'command_key', name='uq_v54_approval_command'),
    sa.UniqueConstraint('organization_id', 'id', 'action_id', 'revision', 'envelope_hash', name='uq_v54_approval_binding'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_approval_scope')
    )
    op.create_table('v54_pending_dispatch',
    sa.Column('action_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('envelope_hash', sa.String(length=64), nullable=False),
    sa.Column('approval_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('pending', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['background_jobs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'approval_id', 'action_id', 'revision', 'envelope_hash'], ['v54_approvals.organization_id', 'v54_approvals.id', 'v54_approvals.action_id', 'v54_approvals.revision', 'v54_approvals.envelope_hash'], name='fk_v54_pending_approval', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('action_id')
    )
    op.create_table('v54_receipts',
    sa.Column('action_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('envelope_hash', sa.String(length=64), nullable=False),
    sa.Column('approval_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('fence', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('target_ref', sa.JSON(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("outcome IN ('APPLIED','NOT_APPLIED','UNKNOWN')", name='ck_v54_receipt_outcome'),
    sa.CheckConstraint('fence > 0', name='ck_v54_receipt_fence'),
    sa.ForeignKeyConstraint(['job_id'], ['background_jobs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'approval_id', 'action_id', 'revision', 'envelope_hash'], ['v54_approvals.organization_id', 'v54_approvals.id', 'v54_approvals.action_id', 'v54_approvals.revision', 'v54_approvals.envelope_hash'], name='fk_v54_receipt_approval', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'action_id', name='uq_v54_single_business_receipt'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_receipt_scope')
    )
    op.create_table('v54_audit_extensions',
    sa.Column('audit_log_id', sa.Integer(), nullable=False),
    sa.Column('subject_type', sa.String(length=40), nullable=False),
    sa.Column('subject_id', sa.String(length=40), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('action_pin', sa.JSON(), nullable=True),
    sa.Column('subject_pin', sa.JSON(), nullable=True),
    sa.Column('approval_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('receipt_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('relation_refs', sa.JSON(), nullable=False),
    sa.Column('correlation_id', sa.String(length=100), nullable=False),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('sequence > 0', name='ck_v54_audit_sequence'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['audit_log_id'], ['audit_logs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['background_jobs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'approval_id'], ['v54_approvals.organization_id', 'v54_approvals.id'], name='fk_v54_audit_approval', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], name='fk_v54_audit_project', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'receipt_id'], ['v54_receipts.organization_id', 'v54_receipts.id'], name='fk_v54_audit_receipt', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('audit_log_id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_audit_scope'),
    sa.UniqueConstraint('organization_id', 'subject_type', 'subject_id', 'sequence', name='uq_v54_audit_sequence')
    )
    op.create_table('v54_context_relations',
    sa.Column('lineage_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('relation_type', sa.String(length=50), nullable=False),
    sa.Column('target_ref', sa.JSON(), nullable=False),
    sa.Column('scope_ref', sa.JSON(), nullable=False),
    sa.Column('expected_target', sa.JSON(), nullable=False),
    sa.Column('expected_context_version', sa.Integer(), nullable=False),
    sa.Column('evidence_pins', sa.JSON(), nullable=False),
    sa.Column('provenance', sa.JSON(), nullable=False),
    sa.Column('state', sa.String(length=20), server_default='hypothesis', nullable=False),
    sa.Column('applicability', sa.String(length=30), server_default='legacy_unverified', nullable=False),
    sa.Column('record_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('confirmed_by', sa.Integer(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('receipt_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.CheckConstraint("relation_type IN ('communication.project','communication.contract','communication.task','communication.draft')", name='ck_v54_context_type'),
    sa.CheckConstraint("state != 'confirmed' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)", name='ck_v54_context_reviewer'),
    sa.CheckConstraint("state IN ('hypothesis','confirmed','rejected','superseded')", name='ck_v54_context_state'),
    sa.CheckConstraint('revision > 0 AND record_version > 0', name='ck_v54_context_version'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'message_id'], ['messages.organization_id', 'messages.id'], name='fk_v54_context_message', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'receipt_id'], ['v54_receipts.organization_id', 'v54_receipts.id'], name='fk_v54_context_receipt', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id', name='uq_v54_context_scope'),
    sa.UniqueConstraint('organization_id', 'lineage_id', 'revision', name='uq_v54_context_lineage'),
    sa.UniqueConstraint('receipt_id')
    )
    op.create_index('ix_v54_pending_dispatch', 'v54_pending_dispatch', ['pending', 'organization_id'], unique=False)
    op.create_index('uq_v54_context_primary', 'v54_context_relations', ['organization_id', 'message_id', 'relation_type'], unique=True, postgresql_where=sa.text("state = 'confirmed' AND relation_type IN ('communication.project','communication.contract')"), sqlite_where=sa.text("state = 'confirmed' AND relation_type IN ('communication.project','communication.contract')"))
    op.add_column('messages', sa.Column('mail_connection_id', sa.Uuid(as_uuid=False), nullable=True))
    op.add_column('messages', sa.Column('provider_message_id', sa.String(length=500), nullable=True))
    op.add_column('messages', sa.Column('source_reference_id', sa.Uuid(as_uuid=False), nullable=True))
    op.add_column('messages', sa.Column('context_version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('messages', sa.Column('analysis_required', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('tasks', sa.Column('record_version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('projects', sa.Column('record_version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('contracts', sa.Column('record_version', sa.Integer(), server_default='1', nullable=False))
    op.create_foreign_key('fk_v54_message_mail', 'messages', 'v54_mail_connections', ['organization_id', 'mail_connection_id'], ['organization_id', 'id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_v54_message_source', 'messages', 'v54_sources', ['organization_id', 'source_reference_id'], ['organization_id', 'id'], ondelete='RESTRICT')
    op.create_unique_constraint('uq_v54_message_mailbox', 'messages', ['mail_connection_id', 'provider_message_id'])
    op.create_check_constraint("ck_v54_message_context_version", "messages", "context_version > 0")
    op.create_check_constraint("ck_v54_message_origin", "messages", "(mail_connection_id IS NULL AND provider_message_id IS NULL AND source_reference_id IS NULL) OR (mail_connection_id IS NOT NULL AND provider_message_id IS NOT NULL AND source_reference_id IS NOT NULL)")


def downgrade():
    # DDL rollback only on an empty isolated pilot schema. Otherwise keep history.
    if op.get_context().as_sql:
        raise RuntimeError("Pilot downgrade requires an online empty-schema check")
    for table in ["v54_action_policies","v54_connection_identities","v54_mail_connections","v54_sources","v54_source_versions","v54_actions","v54_deadline_claims","v54_evidence","v54_source_current","v54_action_revisions","v54_evidence_assessments","v54_approvals","v54_pending_dispatch","v54_receipts","v54_audit_extensions","v54_context_relations"]:
        if op.get_bind().execute(sa.text("SELECT 1 FROM " + table + " LIMIT 1")).first():
            raise RuntimeError("Pilot data exists; use forward recovery, not destructive downgrade")
    op.drop_constraint("fk_v54_message_mail", "messages", type_="foreignkey")
    op.drop_constraint("fk_v54_message_source", "messages", type_="foreignkey")
    op.drop_constraint("uq_v54_message_mailbox", "messages", type_="unique")
    op.drop_constraint("ck_v54_message_origin", "messages", type_="check")
    op.drop_constraint("ck_v54_message_context_version", "messages", type_="check")
    for column in ("analysis_required", "context_version", "source_reference_id",
                   "provider_message_id", "mail_connection_id"):
        op.drop_column("messages", column)
    for table in ("tasks", "projects", "contracts"):
        op.drop_column(table, "record_version")
    for table in ["v54_context_relations","v54_audit_extensions","v54_receipts","v54_pending_dispatch","v54_approvals","v54_evidence_assessments","v54_action_revisions","v54_source_current","v54_evidence","v54_deadline_claims","v54_actions","v54_source_versions","v54_sources","v54_mail_connections","v54_connection_identities","v54_action_policies"]:
        op.drop_table(table)
    op.drop_constraint("uq_v54_message_scope", "messages", type_="unique")
    op.drop_constraint("uq_v54_project_scope", "projects", type_="unique")
