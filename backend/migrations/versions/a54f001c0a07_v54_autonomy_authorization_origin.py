"""Add explicit human-approval or server-policy authorization bindings.

Revision ID: a54f001c0a07
Revises: a54f001c0a06

Existing dispatches are backfilled as HUMAN_APPROVAL.  No policy assignment,
approval, task, provider call, production flag, or external AUTO grant is made.
"""
from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a07"
down_revision = "a54f001c0a06"
branch_labels = None
depends_on = None


_TABLES = ("v54_pending_dispatch", "v54_receipts")


def _add_authorization_columns(table):
    op.add_column(table, sa.Column(
        "authorization_origin", sa.String(20),
        server_default=sa.text("'HUMAN_APPROVAL'"), nullable=True,
    ))
    op.add_column(table, sa.Column("policy_id", sa.Uuid(as_uuid=False), nullable=True))
    op.add_column(table, sa.Column("policy_revision", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("policy_hash", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("authority_epoch", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("decision_hash", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("action_hash", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("payload_hash", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("authorization_decision", sa.JSON(), nullable=True))
    op.add_column(table, sa.Column("authorization_valid_until", sa.DateTime(timezone=True), nullable=True))


def _origin_check(table):
    return (
        "(authorization_origin = 'HUMAN_APPROVAL' AND approval_id IS NOT NULL "
        "AND policy_id IS NULL AND policy_revision IS NULL AND policy_hash IS NULL "
        "AND authority_epoch IS NULL AND decision_hash IS NULL AND action_hash IS NULL "
        "AND payload_hash IS NULL AND authorization_decision IS NULL "
        "AND authorization_valid_until IS NULL) OR "
        "(authorization_origin = 'SERVER_POLICY' AND approval_id IS NULL "
        "AND policy_id IS NOT NULL AND policy_revision IS NOT NULL AND policy_hash IS NOT NULL "
        "AND authority_epoch IS NOT NULL AND decision_hash IS NOT NULL AND action_hash IS NOT NULL "
        "AND payload_hash IS NOT NULL AND authorization_decision IS NOT NULL "
        "AND authorization_valid_until IS NOT NULL)"
    )


def upgrade():
    op.create_unique_constraint(
        "uq_v54_policy_hash_binding", "v54_action_policies",
        ["organization_id", "id", "revision", "policy_hash"],
    )
    for table in _TABLES:
        _add_authorization_columns(table)
        op.execute(sa.text(
            f"UPDATE {table} SET authorization_origin = 'HUMAN_APPROVAL' "
            "WHERE authorization_origin IS NULL"
        ))
        op.alter_column(table, "authorization_origin", nullable=False)
        op.alter_column(table, "approval_id", existing_type=sa.Uuid(as_uuid=False), nullable=True)
        op.create_check_constraint(
            f"ck_{table}_authorization_origin", table,
            "authorization_origin IN ('HUMAN_APPROVAL','SERVER_POLICY')",
        )
        op.create_check_constraint(
            f"ck_{table}_authorization_exclusive", table, _origin_check(table),
        )
        op.create_check_constraint(
            f"ck_{table}_authorization_values", table,
            "authorization_origin != 'SERVER_POLICY' OR "
            "(policy_revision > 0 AND authority_epoch > 0 "
            "AND length(policy_hash) = 64 AND length(decision_hash) = 64 "
            "AND length(action_hash) = 64 AND length(envelope_hash) = 64 "
            "AND length(payload_hash) = 64)",
        )
        op.create_foreign_key(
            f"fk_{table}_sealed_action", table, "v54_action_revisions",
            ["organization_id", "action_id", "revision", "envelope_hash"],
            ["organization_id", "action_id", "revision", "envelope_hash"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{table}_server_policy", table, "v54_action_policies",
            ["organization_id", "policy_id", "policy_revision", "policy_hash"],
            ["organization_id", "id", "revision", "policy_hash"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table}_authorization", table,
            ["authorization_origin", "organization_id", "policy_id", "policy_revision"],
        )


def downgrade():
    connection = op.get_bind()
    for table in _TABLES:
        if connection.scalar(sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {table} "
            "WHERE authorization_origin = 'SERVER_POLICY' LIMIT 1)"
        )):
            raise RuntimeError("SERVER_POLICY authorization data requires explicit archival")
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_authorization", table_name=table)
        op.drop_constraint(f"fk_{table}_server_policy", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_sealed_action", table, type_="foreignkey")
        op.drop_constraint(f"ck_{table}_authorization_values", table, type_="check")
        op.drop_constraint(f"ck_{table}_authorization_exclusive", table, type_="check")
        op.drop_constraint(f"ck_{table}_authorization_origin", table, type_="check")
        op.alter_column(table, "approval_id", existing_type=sa.Uuid(as_uuid=False), nullable=False)
        for column in (
            "authorization_valid_until", "authorization_decision", "payload_hash", "action_hash",
            "decision_hash", "authority_epoch", "policy_hash", "policy_revision", "policy_id",
            "authorization_origin",
        ):
            op.drop_column(table, column)
    op.drop_constraint("uq_v54_policy_hash_binding", "v54_action_policies", type_="unique")
