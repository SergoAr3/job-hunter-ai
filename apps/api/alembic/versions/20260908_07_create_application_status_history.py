"""create application status history

Revision ID: 20260908_07
Revises: 20260907_06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_07"
down_revision = "20260907_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('saved', 'applied', 'interview', 'offer', 'rejected')",
            name="ck_application_status_history_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_application_status_history_application_occurred_id",
        "application_status_history",
        ["application_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_status_history_application_occurred_id",
        table_name="application_status_history",
    )
    op.drop_table("application_status_history")
