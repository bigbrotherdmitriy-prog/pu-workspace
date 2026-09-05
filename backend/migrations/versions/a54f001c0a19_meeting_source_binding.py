"""Exact human meeting source binding; legacy origins remain explicitly unbound."""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a19"
down_revision = "a54f001c0a18"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("meetings", sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_meeting_record_version", "meetings", "record_version > 0")
    op.create_unique_constraint("uq_meeting_project_id", "meetings", ["project_id", "id"])
    op.create_table(
        "meeting_source_bindings",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("meeting_record_version", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("source_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("command_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("bound_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id", "project_id"], ["projects.organization_id", "projects.id"],
            name="fk_meeting_binding_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "meeting_id"], ["meetings.project_id", "meetings.id"],
            name="fk_meeting_binding_meeting", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_id", "source_version_id"],
            ["v54_source_versions.organization_id", "v54_source_versions.source_id", "v54_source_versions.id"],
            name="fk_meeting_binding_observation", ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "meeting_id", "id", name="uq_meeting_binding_origin"),
        sa.UniqueConstraint("meeting_id", "meeting_record_version", name="uq_meeting_binding_version"),
        sa.UniqueConstraint("meeting_id", "command_id", name="uq_meeting_binding_command"),
        sa.CheckConstraint("meeting_record_version > 1", name="ck_meeting_binding_version"),
    )
    op.create_index("ix_meeting_source_bindings_meeting_id", "meeting_source_bindings", ["meeting_id"])
    op.add_column("management_proposal_origins", sa.Column("meeting_source_binding_id", sa.Uuid(as_uuid=False)))
    op.create_check_constraint("ck_proposal_meeting_binding_kind", "management_proposal_origins",
        "meeting_source_binding_id IS NULL OR origin_type = 'meeting'")
    op.create_foreign_key("fk_proposal_meeting_binding", "management_proposal_origins", "meeting_source_bindings",
        ["project_id", "origin_id", "meeting_source_binding_id"], ["project_id", "meeting_id", "id"], ondelete="RESTRICT")
    # DB-level protection includes Core SQL, not only ORM events.
    op.execute("""CREATE FUNCTION deny_meeting_binding_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'meeting_source_binding_is_append_only'; END;
        $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER meeting_binding_immutable BEFORE UPDATE OR DELETE
        ON meeting_source_bindings FOR EACH ROW EXECUTE FUNCTION deny_meeting_binding_mutation()""")


def downgrade():
    op.drop_constraint("fk_proposal_meeting_binding", "management_proposal_origins", type_="foreignkey")
    op.drop_constraint("ck_proposal_meeting_binding_kind", "management_proposal_origins", type_="check")
    op.drop_column("management_proposal_origins", "meeting_source_binding_id")
    op.drop_table("meeting_source_bindings")
    op.execute("DROP FUNCTION deny_meeting_binding_mutation()")
    op.drop_constraint("uq_meeting_project_id", "meetings", type_="unique")
    op.drop_constraint("ck_meeting_record_version", "meetings", type_="check")
    op.drop_column("meetings", "record_version")
