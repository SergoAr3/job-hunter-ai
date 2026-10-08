"""Add the derived skill identity layer; no semantic data backfill."""
from alembic import op
import sqlalchemy as sa

revision = "20261009_20"
down_revision = "20261006_19"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(100), nullable=False),
        sa.Column("normalized_key", sa.String(512).with_variant(sa.String(512, collation="C"), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_skills"),
        sa.UniqueConstraint("normalized_key", name="uq_skills_normalized_key"),
        sa.CheckConstraint("length(canonical_name) BETWEEN 1 AND 100 AND length(trim(canonical_name)) > 0", name="ck_skills_name_length"),
        sa.CheckConstraint("length(normalized_key) BETWEEN 1 AND 512 AND length(trim(normalized_key)) > 0", name="ck_skills_key_length"),
    )
    op.create_table(
        "skill_aliases",
        sa.Column("normalized_alias", sa.String(512).with_variant(sa.String(512, collation="C"), "postgresql"), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("normalized_alias", name="pk_skill_aliases"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name="fk_skill_aliases_skill", ondelete="CASCADE"),
        sa.CheckConstraint("length(alias) BETWEEN 1 AND 100 AND length(trim(alias)) > 0", name="ck_skill_aliases_name_length"),
        sa.CheckConstraint("length(normalized_alias) BETWEEN 1 AND 512 AND length(trim(normalized_alias)) > 0", name="ck_skill_aliases_key_length"),
    )
    op.create_index("ix_skill_aliases_skill_id", "skill_aliases", ["skill_id"])
    op.create_table(
        "job_skills",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("requirement_kind", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "skill_id", "requirement_kind", name="pk_job_skills"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_job_skills_job", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name="fk_job_skills_skill", ondelete="RESTRICT"),
        sa.CheckConstraint("requirement_kind IN ('required','preferred')", name="ck_job_skills_requirement_kind"),
    )
    op.create_table(
        "user_skills",
        sa.Column("user_profile_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("user_profile_id", "skill_id", name="pk_user_skills"),
        sa.ForeignKeyConstraint(["user_profile_id"], ["user_profiles.id"], name="fk_user_skills_profile", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name="fk_user_skills_skill", ondelete="RESTRICT"),
    )


def downgrade():
    op.drop_table("user_skills")
    op.drop_table("job_skills")
    op.drop_index("ix_skill_aliases_skill_id", table_name="skill_aliases")
    op.drop_table("skill_aliases")
    op.drop_table("skills")
