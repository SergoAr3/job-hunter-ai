"""create profile experience facts

Revision ID: 20260911_09
Revises: 20260908_08
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260911_09"
down_revision = "20260908_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_experience_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_profile_experience_facts_user_profile_id", "profile_experience_facts", ["user_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_profile_experience_facts_user_profile_id", table_name="profile_experience_facts")
    op.drop_table("profile_experience_facts")
