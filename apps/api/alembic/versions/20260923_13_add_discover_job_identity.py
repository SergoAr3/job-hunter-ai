"""Add external identity and freshness fields for Discover jobs."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_13"
down_revision = "20260922_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("external_id", sa.String(length=255)))
        batch.add_column(sa.Column("source_scope", sa.String(length=255)))
        batch.add_column(sa.Column("source_updated_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("fetched_at", sa.DateTime(timezone=True)))
        batch.drop_constraint("ck_jobs_source", type_="check")
        batch.drop_constraint("ck_jobs_ingestion_method", type_="check")
        batch.create_check_constraint(
            "ck_jobs_source",
            "source IN ('linkedin', 'hh', 'greenhouse', 'lever', 'company_site', 'trudvsem')",
        )
        batch.create_check_constraint(
            "ck_jobs_ingestion_method",
            "ingestion_method IN ('manual', 'discover')",
        )
        batch.create_check_constraint(
            "ck_jobs_external_identity_block",
            "(external_id IS NULL AND source_scope IS NULL) OR "
            "(external_id IS NOT NULL AND source_scope IS NOT NULL)",
        )
        batch.create_unique_constraint(
            "uq_jobs_external_identity", ["source", "source_scope", "external_id"]
        )


def downgrade() -> None:
    connection = op.get_bind()
    incompatible = connection.execute(
        sa.text(
            "SELECT count(*) FROM jobs WHERE source = 'trudvsem' "
            "OR ingestion_method = 'discover' OR external_id IS NOT NULL "
            "OR source_scope IS NOT NULL OR source_updated_at IS NOT NULL OR fetched_at IS NOT NULL"
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError("Cannot downgrade while Discover job data exists")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("uq_jobs_external_identity", type_="unique")
        batch.drop_constraint("ck_jobs_external_identity_block", type_="check")
        batch.drop_constraint("ck_jobs_source", type_="check")
        batch.drop_constraint("ck_jobs_ingestion_method", type_="check")
        batch.create_check_constraint(
            "ck_jobs_source",
            "source IN ('linkedin', 'hh', 'greenhouse', 'lever', 'company_site')",
        )
        batch.create_check_constraint(
            "ck_jobs_ingestion_method", "ingestion_method IN ('manual')"
        )
        batch.drop_column("fetched_at")
        batch.drop_column("source_updated_at")
        batch.drop_column("source_scope")
        batch.drop_column("external_id")
