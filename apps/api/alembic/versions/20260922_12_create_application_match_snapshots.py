"""Create immutable application match snapshots."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260922_12"
down_revision = "20260922_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON(none_as_null=True).with_variant(
        postgresql.JSONB(none_as_null=True), "postgresql"
    )
    op.create_table(
        "application_match_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("trigger_status_history_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("capture_status", sa.String(length=16), nullable=False),
        sa.Column("unavailable_reason", sa.String(length=32)),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("algorithm_version", sa.String(length=64)),
        sa.Column("score", sa.Integer()),
        sa.Column("verdict", sa.String(length=32)),
        sa.Column("coverage", sa.Integer()),
        sa.Column("confidence", sa.String(length=16)),
        sa.Column("profile_updated_at", sa.DateTime(timezone=True)),
        sa.Column("job_updated_at", sa.DateTime(timezone=True)),
        sa.Column("job_parsing_status", sa.String(length=16)),
        sa.Column("job_ai_enrichment_status", sa.String(length=16)),
        sa.Column("inputs", json_type),
        sa.Column("result_detail", json_type),
        sa.CheckConstraint(
            "snapshot_schema_version = 1",
            name="ck_application_match_snapshots_schema_version",
        ),
        sa.CheckConstraint(
            "capture_status IN ('captured', 'unavailable')",
            name="ck_application_match_snapshots_capture_status",
        ),
        sa.CheckConstraint(
            "unavailable_reason IS NULL OR unavailable_reason IN ('profile_missing', 'matcher_error')",
            name="ck_application_match_snapshots_unavailable_reason",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_application_match_snapshots_score",
        ),
        sa.CheckConstraint(
            "coverage IS NULL OR coverage BETWEEN 0 AND 100",
            name="ck_application_match_snapshots_coverage",
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('insufficient_data', 'low', 'medium', 'high')",
            name="ck_application_match_snapshots_verdict",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name="ck_application_match_snapshots_confidence",
        ),
        sa.CheckConstraint(
            "(capture_status = 'captured' AND unavailable_reason IS NULL "
            "AND algorithm_version IS NOT NULL AND verdict IS NOT NULL AND coverage IS NOT NULL "
            "AND profile_updated_at IS NOT NULL AND job_updated_at IS NOT NULL "
            "AND job_parsing_status IS NOT NULL AND job_ai_enrichment_status IS NOT NULL "
            "AND inputs IS NOT NULL AND result_detail IS NOT NULL) OR "
            "(capture_status = 'unavailable' AND unavailable_reason IS NOT NULL "
            "AND algorithm_version IS NULL AND score IS NULL AND verdict IS NULL "
            "AND coverage IS NULL AND confidence IS NULL AND profile_updated_at IS NULL "
            "AND job_updated_at IS NULL AND job_parsing_status IS NULL "
            "AND job_ai_enrichment_status IS NULL AND inputs IS NULL AND result_detail IS NULL)",
            name="ck_application_match_snapshots_capture_payload",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["trigger_status_history_id"],
            ["application_status_history.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "application_id", name="uq_application_match_snapshots_application_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("application_match_snapshots")
