"""create user profiles table

Revision ID: 20260830_05
Revises: 20260829_04
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260830_05"
down_revision = "20260829_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "target_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "skills",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("experience", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column(
            "location",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("workplace_preference", sa.String(length=16), server_default="any", nullable=False),
        sa.Column("salary_min", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
        sa.Column("salary_period", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column(
            "languages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "jsonb_array_length(target_roles) > 0",
            name="ck_user_profiles_target_roles_not_empty",
        ),
        sa.CheckConstraint(
            "experience IN ('intern', 'junior', 'middle', 'senior', 'lead', 'unknown')",
            name="ck_user_profiles_experience",
        ),
        sa.CheckConstraint(
            "workplace_preference IN ('remote', 'hybrid', 'onsite', 'any')",
            name="ck_user_profiles_workplace_preference",
        ),
        sa.CheckConstraint(
            "salary_period IN ('month', 'year', 'unknown')",
            name="ck_user_profiles_salary_period",
        ),
        sa.CheckConstraint(
            "(salary_min IS NULL AND salary_currency IS NULL AND salary_period = 'unknown') OR "
            "(salary_min > 0 AND salary_currency IS NOT NULL AND salary_period IN ('month', 'year'))",
            name="ck_user_profiles_salary_block",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
