"""Create confirmed work history with partial date precision."""
from alembic import op
import sqlalchemy as sa

revision = "20260912_10"
down_revision = "20260911_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "work_experiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company", sa.String(200)), sa.Column("position", sa.String(200)),
        sa.Column("engagement_kind", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("start_year", sa.Integer()), sa.Column("start_month", sa.Integer()),
        sa.Column("end_year", sa.Integer()), sa.Column("end_month", sa.Integer()),
        sa.Column("is_current", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("company IS NOT NULL OR position IS NOT NULL", name="ck_work_identity"),
        sa.CheckConstraint("engagement_kind IN ('employment','internship','freelance','unknown')", name="ck_work_kind"),
        sa.CheckConstraint("start_year IS NULL OR start_year BETWEEN 1900 AND 9999", name="ck_work_start_year"),
        sa.CheckConstraint("end_year IS NULL OR end_year BETWEEN 1900 AND 9999", name="ck_work_end_year"),
        sa.CheckConstraint("start_month IS NULL OR (start_year IS NOT NULL AND start_month BETWEEN 1 AND 12)", name="ck_work_start_month"),
        sa.CheckConstraint("end_month IS NULL OR (end_year IS NOT NULL AND end_month BETWEEN 1 AND 12)", name="ck_work_end_month"),
        sa.CheckConstraint("end_year IS NULL OR is_current IS FALSE", name="ck_work_current"),
        sa.CheckConstraint("start_year IS NULL OR end_year IS NULL OR start_year < end_year OR (start_year = end_year AND coalesce(start_month,1) <= coalesce(end_month,12))", name="ck_work_order"),
    )
    op.create_index("ix_work_experiences_user_profile_id", "work_experiences", ["user_profile_id"])


def downgrade():
    op.drop_index("ix_work_experiences_user_profile_id", table_name="work_experiences")
    op.drop_table("work_experiences")
