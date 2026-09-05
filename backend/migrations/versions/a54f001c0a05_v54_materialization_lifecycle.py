"""Add durable encrypted materialization lifecycle.

Revision ID: a54f001c0a05
Revises: a54f001c0a04
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a05"
down_revision = "a54f001c0a04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_v54_evidence_binding", "v54_evidence",
        ["organization_id", "id", "source_id", "source_version_id"],
    )
    op.create_table(
        "v54_materializations",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("source_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("evidence_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("parent_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("object_id", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), server_default="ADMITTED", nullable=False),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("active_fence", sa.String(32), nullable=True),
        sa.Column("kek_reference", sa.String(255), nullable=False),
        sa.Column("kek_version", sa.String(255), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=True),
        sa.Column("wrapped_dek", sa.String(255), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("residency", sa.String(100), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("copy_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("derive_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("writing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("record_version > 0", name="ck_v54_materialization_version"),
        sa.CheckConstraint(
            "state IN ('ADMITTED','WRITING','SEALED','DERIVED','EXPIRED','PURGED')",
            name="ck_v54_materialization_state",
        ),
        sa.CheckConstraint("length(object_id) = 32", name="ck_v54_materialization_object_id"),
        sa.CheckConstraint("active_fence IS NULL OR length(active_fence) = 32",
                           name="ck_v54_materialization_fence"),
        sa.CheckConstraint("chunk_size IS NULL OR chunk_size > 0",
                           name="ck_v54_materialization_chunk_size"),
        sa.CheckConstraint("format_version IS NULL OR format_version > 0",
                           name="ck_v54_materialization_format"),
        sa.CheckConstraint(
            "(state = 'ADMITTED' AND active_fence IS NULL AND format_version IS NULL "
            "AND chunk_size IS NULL AND wrapped_dek IS NULL AND manifest IS NULL "
            "AND writing_at IS NULL AND sealed_at IS NULL AND derived_at IS NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'WRITING' AND active_fence IS NOT NULL AND writing_at IS NOT NULL "
            "AND format_version IS NULL AND chunk_size IS NULL AND wrapped_dek IS NULL "
            "AND manifest IS NULL AND sealed_at IS NULL AND derived_at IS NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'SEALED' AND active_fence IS NOT NULL AND writing_at IS NOT NULL "
            "AND format_version IS NOT NULL AND chunk_size IS NOT NULL AND wrapped_dek IS NOT NULL "
            "AND manifest IS NOT NULL AND sealed_at IS NOT NULL AND derived_at IS NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'DERIVED' AND active_fence IS NULL AND writing_at IS NOT NULL "
            "AND format_version IS NOT NULL AND chunk_size IS NOT NULL AND wrapped_dek IS NOT NULL "
            "AND manifest IS NOT NULL AND sealed_at IS NOT NULL AND derived_at IS NOT NULL "
            "AND expired_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'EXPIRED' AND active_fence IS NULL AND expired_at IS NOT NULL "
            "AND purged_at IS NULL) OR "
            "(state = 'PURGED' AND active_fence IS NULL AND format_version IS NULL "
            "AND chunk_size IS NULL AND wrapped_dek IS NULL AND manifest IS NOT NULL "
            "AND expired_at IS NOT NULL AND purged_at IS NOT NULL)",
            name="ck_v54_materialization_shape",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"], ["projects.organization_id", "projects.id"],
            name="fk_v54_materialization_project", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            ["v54_source_versions.organization_id", "v54_source_versions.source_id",
             "v54_source_versions.id"],
            name="fk_v54_materialization_observation", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_id", "source_id", "source_version_id"],
            ["v54_evidence.organization_id", "v54_evidence.id", "v54_evidence.source_id",
             "v54_evidence.source_version_id"],
            name="fk_v54_materialization_evidence", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_id"],
            ["v54_materializations.organization_id", "v54_materializations.id"],
            name="fk_v54_materialization_parent", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_v54_materialization_scope"),
        sa.UniqueConstraint("organization_id", "object_id", name="uq_v54_materialization_object"),
    )
    op.create_index("ix_v54_materialization_retention", "v54_materializations",
                    ["state", "retention_until"])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM v54_materializations)")):
        raise RuntimeError("Materialization tombstones cannot be removed safely")
    op.drop_index("ix_v54_materialization_retention", table_name="v54_materializations")
    op.drop_table("v54_materializations")
    op.drop_constraint("uq_v54_evidence_binding", "v54_evidence", type_="unique")
