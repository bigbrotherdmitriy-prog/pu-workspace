"""Fence local document identity and add service retention audit origin.

Revision ID: a54f001c0a08
Revises: a54f001c0a07
"""
from alembic import context, op
import sqlalchemy as sa


revision = "a54f001c0a08"
down_revision = "a54f001c0a07"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        connection = op.get_bind()
        duplicate = connection.scalar(sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM documents "
            "WHERE source = 'local_upload' AND external_id IS NOT NULL "
            "GROUP BY project_id, external_id HAVING count(*) > 1"
            ")"
        ))
        if duplicate:
            raise RuntimeError("Duplicate local-upload document identity requires review")
    op.create_index(
        "uq_documents_local_upload_identity", "documents",
        ["project_id", "external_id"], unique=True,
        postgresql_where=sa.text("source = 'local_upload' AND external_id IS NOT NULL"),
    )
    op.add_column(
        "v54_audit_extensions",
        sa.Column("service_principal", sa.String(100), nullable=True),
    )
    op.alter_column(
        "v54_audit_extensions", "actor_id",
        existing_type=sa.Integer(), nullable=True,
    )
    op.create_check_constraint(
        "ck_v54_audit_actor_origin", "v54_audit_extensions",
        "(actor_id IS NOT NULL AND service_principal IS NULL) OR "
        "(actor_id IS NULL AND service_principal IS NOT NULL)",
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM v54_audit_extensions "
        "WHERE service_principal IS NOT NULL LIMIT 1)"
    )):
        raise RuntimeError("Service retention audit records require explicit archival")
    op.drop_constraint(
        "ck_v54_audit_actor_origin", "v54_audit_extensions", type_="check",
    )
    op.alter_column(
        "v54_audit_extensions", "actor_id",
        existing_type=sa.Integer(), nullable=False,
    )
    op.drop_column("v54_audit_extensions", "service_principal")
    op.drop_index("uq_documents_local_upload_identity", table_name="documents")
