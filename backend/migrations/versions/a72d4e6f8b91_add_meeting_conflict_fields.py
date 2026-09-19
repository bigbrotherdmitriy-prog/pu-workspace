"""Add structured meeting participants and duration.

Revision ID: a72d4e6f8b91
Revises: f91c2d4e6a80
"""

from alembic import op
import sqlalchemy as sa


revision = "a72d4e6f8b91"
down_revision = "f91c2d4e6a80"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("meetings", sa.Column("duration_minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_meetings_duration_minutes",
        "meetings",
        "duration_minutes IS NULL OR (duration_minutes >= 1 AND duration_minutes <= 10080)",
    )
    op.create_table(
        "meeting_participants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "meeting_id", sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column(
            "contact_id", sa.Integer(),
            sa.ForeignKey("project_contacts.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND contact_id IS NULL) OR "
            "(user_id IS NULL AND contact_id IS NOT NULL)",
            name="ck_meeting_participants_one_identity",
        ),
        sa.UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant_user"),
        sa.UniqueConstraint("meeting_id", "contact_id", name="uq_meeting_participant_contact"),
    )
    for column in ("meeting_id", "user_id", "contact_id"):
        op.create_index(f"ix_meeting_participants_{column}", "meeting_participants", [column])


def downgrade():
    op.drop_table("meeting_participants")
    op.drop_constraint("ck_meetings_duration_minutes", "meetings", type_="check")
    op.drop_column("meetings", "duration_minutes")
