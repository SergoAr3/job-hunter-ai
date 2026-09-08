"""add application next action

Revision ID: 20260908_08
Revises: 20260908_07
"""

from alembic import op
import sqlalchemy as sa

revision = "20260908_08"
down_revision = "20260908_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("next_action", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("next_action_due_on", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_applications_next_action_block", "applications",
        "(next_action IS NULL AND next_action_due_on IS NULL) OR "
        "(next_action IS NOT NULL AND next_action_due_on IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_applications_next_action_block", "applications", type_="check")
    op.drop_column("applications", "next_action_due_on")
    op.drop_column("applications", "next_action")
