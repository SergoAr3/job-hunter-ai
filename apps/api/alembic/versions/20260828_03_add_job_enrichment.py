"""add job enrichment fields

Revision ID: 20260828_03
Revises: 20260826_02
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260828_03"
down_revision = "20260826_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("ingestion_method", sa.String(length=16), server_default="manual", nullable=False))
    op.add_column("jobs", sa.Column("requirements_text", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("salary_text", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("salary_min", sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column("jobs", sa.Column("salary_max", sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column("jobs", sa.Column("salary_currency", sa.String(length=3), nullable=True))
    op.add_column("jobs", sa.Column("salary_period", sa.String(length=16), server_default="unknown", nullable=False))
    op.add_column("jobs", sa.Column("salary_period_inferred", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("jobs", sa.Column("location", sa.String(length=512), nullable=True))
    op.add_column("jobs", sa.Column("workplace_type", sa.String(length=16), server_default="unknown", nullable=False))
    op.add_column("jobs", sa.Column("employment_type", sa.String(length=16), server_default="unknown", nullable=False))
    op.add_column("jobs", sa.Column("parsing_status", sa.String(length=16), server_default="not_attempted", nullable=False))
    op.add_column("jobs", sa.Column("parsing_error", sa.String(length=128), nullable=True))
    op.add_column("jobs", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))

    # Existing rows were created before source and ingestion method were split.
    op.execute("UPDATE jobs SET ingestion_method = 'manual'")
    op.execute(
        """
        UPDATE jobs
        SET source = CASE
            WHEN lower(source_url) ~ '^https?://([^/]+\\.)?linkedin\\.com([/:?#]|$)' THEN 'linkedin'
            WHEN lower(source_url) ~ '^https?://([^/]+\\.)?hh\\.ru([/:?#]|$)' THEN 'hh'
            WHEN lower(source_url) ~ '^https?://(boards|job-boards)\\.greenhouse\\.io([/:?#]|$)' THEN 'greenhouse'
            WHEN lower(source_url) ~ '^https?://jobs\\.lever\\.co([/:?#]|$)' THEN 'lever'
            ELSE 'company_site'
        END
        """
    )
    op.execute("UPDATE jobs SET parsing_status = 'not_attempted'")

    op.create_check_constraint("ck_jobs_source", "jobs", "source IN ('linkedin', 'hh', 'greenhouse', 'lever', 'company_site')")
    op.create_check_constraint("ck_jobs_ingestion_method", "jobs", "ingestion_method IN ('manual')")
    op.create_check_constraint("ck_jobs_salary_period", "jobs", "salary_period IN ('hour', 'day', 'week', 'month', 'year', 'unknown')")
    op.create_check_constraint("ck_jobs_workplace_type", "jobs", "workplace_type IN ('remote', 'hybrid', 'onsite', 'unknown')")
    op.create_check_constraint("ck_jobs_employment_type", "jobs", "employment_type IN ('full_time', 'part_time', 'contract', 'internship', 'temporary', 'unknown')")
    op.create_check_constraint("ck_jobs_parsing_status", "jobs", "parsing_status IN ('not_attempted', 'pending', 'success', 'partial', 'failed')")
    op.create_check_constraint("ck_jobs_salary_min_nonnegative", "jobs", "salary_min IS NULL OR salary_min >= 0")
    op.create_check_constraint("ck_jobs_salary_max_nonnegative", "jobs", "salary_max IS NULL OR salary_max >= 0")
    op.create_check_constraint("ck_jobs_salary_range", "jobs", "salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min")


def downgrade() -> None:
    for name in (
        "ck_jobs_salary_range", "ck_jobs_salary_max_nonnegative", "ck_jobs_salary_min_nonnegative",
        "ck_jobs_parsing_status", "ck_jobs_employment_type", "ck_jobs_workplace_type",
        "ck_jobs_salary_period", "ck_jobs_ingestion_method", "ck_jobs_source",
    ):
        op.drop_constraint(name, "jobs", type_="check")
    for name in (
        "updated_at", "parsing_error", "parsing_status", "employment_type", "workplace_type", "location",
        "salary_period_inferred", "salary_period", "salary_currency", "salary_max", "salary_min",
        "salary_text", "requirements_text", "ingestion_method",
    ):
        op.drop_column("jobs", name)
