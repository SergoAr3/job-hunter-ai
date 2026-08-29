"""add job AI enrichment fields

Revision ID: 20260829_04
Revises: 20260828_03
Create Date: 2026-08-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260829_04"
down_revision = "20260828_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "required_skills",
        "nice_to_have_skills",
        "experience_requirements",
        "language_requirements",
        "responsibilities",
    ):
        op.add_column(
            "jobs",
            sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        )
    op.add_column("jobs", sa.Column("seniority", sa.String(length=16), server_default="unknown", nullable=False))
    op.add_column("jobs", sa.Column("ai_enrichment_status", sa.String(length=16), server_default="not_attempted", nullable=False))
    op.add_column("jobs", sa.Column("ai_enrichment_error", sa.String(length=128), nullable=True))

    op.create_check_constraint("ck_jobs_seniority", "jobs", "seniority IN ('intern', 'junior', 'middle', 'senior', 'lead', 'unknown')")
    op.create_check_constraint("ck_jobs_ai_enrichment_status", "jobs", "ai_enrichment_status IN ('not_attempted', 'pending', 'success', 'failed')")
    op.create_check_constraint("ck_jobs_ai_enrichment_error", "jobs", "ai_enrichment_error IS NULL OR ai_enrichment_error IN ('timeout', 'invalid_output', 'provider_error', 'processing_failed')")


def downgrade() -> None:
    for name in ("ck_jobs_ai_enrichment_error", "ck_jobs_ai_enrichment_status", "ck_jobs_seniority"):
        op.drop_constraint(name, "jobs", type_="check")
    for name in (
        "ai_enrichment_error",
        "ai_enrichment_status",
        "seniority",
        "responsibilities",
        "language_requirements",
        "experience_requirements",
        "nice_to_have_skills",
        "required_skills",
    ):
        op.drop_column("jobs", name)
