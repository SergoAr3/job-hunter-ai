"""Browser-bound Telegram login/link challenges; preserve accounts and sessions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261002_16"
down_revision = "20261002_15"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_telegram_challenges",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(8), nullable=False),
        sa.Column("initiating_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("initiating_session_hash", sa.String(64), sa.ForeignKey("auth_sessions.token_hash", ondelete="CASCADE")),
        sa.Column("approved_telegram_id", sa.BigInteger()),
        sa.Column("telegram_metadata", sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purpose IN ('login','link')", name="ck_telegram_challenge_purpose"),
        sa.CheckConstraint("(purpose = 'login' AND initiating_user_id IS NULL AND initiating_session_hash IS NULL) OR (purpose = 'link' AND initiating_user_id IS NOT NULL AND initiating_session_hash IS NOT NULL)", name="ck_telegram_challenge_initiator"),
        sa.CheckConstraint("expires_at > created_at", name="ck_telegram_challenge_expiry"),
        sa.CheckConstraint("(approved_at IS NULL AND approved_telegram_id IS NULL AND telegram_metadata IS NULL) OR (approved_at IS NOT NULL AND approved_telegram_id IS NOT NULL AND telegram_metadata IS NOT NULL)", name="ck_telegram_challenge_approval"),
        sa.CheckConstraint("(consumed_at IS NULL AND outcome IS NULL) OR (consumed_at IS NOT NULL AND outcome IS NOT NULL AND outcome IN ('completed','conflict','cancelled'))", name="ck_telegram_challenge_outcome"),
    )
    op.create_index("ix_auth_telegram_challenges_expires_at", "auth_telegram_challenges", ["expires_at"])
    op.create_index("ix_auth_telegram_challenges_initiating_user_id", "auth_telegram_challenges", ["initiating_user_id"])


def downgrade():
    op.drop_table("auth_telegram_challenges")
