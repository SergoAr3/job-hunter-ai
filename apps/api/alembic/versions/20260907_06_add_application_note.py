"""add application note

Revision ID: 20260907_06
Revises: 20260830_05
"""

from alembic import op
import sqlalchemy as sa

revision = "20260907_06"
down_revision = "20260830_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "note")
