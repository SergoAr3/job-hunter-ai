"""One-time email ownership/recovery tokens; no identity backfill."""
from alembic import op
import sqlalchemy as sa
revision = "20261003_17"
down_revision = "20261002_16"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_email_tokens",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(8)),
        sa.CheckConstraint("purpose IN ('verify','reset')", name="ck_auth_email_tokens_purpose"),
        sa.CheckConstraint("expires_at > created_at", name="ck_auth_email_tokens_expiry"),
        sa.CheckConstraint("(consumed_at IS NULL AND outcome IS NULL) OR (consumed_at IS NOT NULL AND outcome IS NOT NULL AND outcome IN ('consumed','replaced'))", name="ck_auth_email_tokens_terminal"),
    )
    op.create_index("ix_auth_email_tokens_user_purpose", "auth_email_tokens", ["user_id", "purpose"])
    op.create_index("ix_auth_email_tokens_expires_at", "auth_email_tokens", ["expires_at"])


def downgrade():
    op.drop_table("auth_email_tokens")
