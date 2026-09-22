"""Scope mailbox rollout to an explicit project cohort.

Revision ID: a54f001c0a11
Revises: a54f001c0a10
"""
from alembic import context, op
import sqlalchemy as sa


revision = "a54f001c0a11"
down_revision = "a54f001c0a10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v54_mailbox_project_cohorts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("mail_connection_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            ondelete="RESTRICT",
            name="fk_v54_mailbox_cohort_project",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "mail_connection_id"],
            ["v54_mail_connections.organization_id", "v54_mail_connections.id"],
            ondelete="RESTRICT",
            name="fk_v54_mailbox_cohort_mail",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"], ["users.id"], ondelete="RESTRICT",
            name="fk_v54_mailbox_cohort_actor",
        ),
        sa.UniqueConstraint(
            "organization_id", "project_id", "mail_connection_id", "credential_generation",
            name="uq_v54_mailbox_project_cohort",
        ),
        sa.CheckConstraint(
            "credential_generation > 0 AND record_version > 0",
            name="ck_v54_mailbox_cohort_versions",
        ),
    )

    # Existing OAuth mappings are inventoried but remain explicitly disabled.
    # No project is enrolled merely because it shares a verified account.
    op.execute(sa.text("""
        INSERT INTO v54_mailbox_project_cohorts
            (organization_id, project_id, mail_connection_id,
             credential_generation, enabled, record_version, changed_at)
        SELECT p.organization_id, t.project_id, m.id, g.generation,
               false, 1, CURRENT_TIMESTAMP
          FROM google_oauth_tokens AS t
          JOIN projects AS p ON p.id = t.project_id
          JOIN v54_mailbox_credential_generations AS g
            ON g.google_token_id = t.id
          JOIN v54_mail_connections AS m
            ON m.organization_id = g.organization_id
           AND m.identity_id = g.connection_identity_id
           AND m.namespace = 'gmail'
        ON CONFLICT DO NOTHING
    """))


def downgrade():
    message = "Enabled mailbox project cohorts must be disabled before downgrade"
    check = sa.text(
        "SELECT EXISTS (SELECT 1 FROM v54_mailbox_project_cohorts "
        "WHERE enabled IS TRUE LIMIT 1)"
    )
    if context.is_offline_mode():
        op.execute(sa.text(f"""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM v54_mailbox_project_cohorts
                           WHERE enabled IS TRUE LIMIT 1)
                THEN RAISE EXCEPTION '{message}'; END IF;
            END $$
        """))
    elif op.get_bind().scalar(check):
        raise RuntimeError(message)
    op.drop_table("v54_mailbox_project_cohorts")
