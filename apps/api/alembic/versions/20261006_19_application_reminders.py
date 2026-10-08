"""One exact reminder per Application; no date-only backfill."""
from alembic import op
import sqlalchemy as sa
revision = "20261006_19"
down_revision = "20261005_18"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("application_reminders",
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("generation", sa.Uuid(), nullable=False),
        sa.Column("delivery_state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(32)),
        sa.CheckConstraint("delivery_state IN ('pending','claimed','sent','failed')", name="ck_reminders_state"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_reminders_attempts"),
        sa.CheckConstraint("(delivery_state = 'claimed' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (delivery_state <> 'claimed' AND lease_token IS NULL AND lease_until IS NULL)", name="ck_reminders_lease"),
        sa.CheckConstraint("(delivery_state = 'sent' AND sent_at IS NOT NULL) OR (delivery_state <> 'sent' AND sent_at IS NULL)", name="ck_reminders_sent"),
        sa.CheckConstraint("last_error_code IS NULL OR last_error_code IN ('CONNECT_FAILED','RATE_LIMITED','PROVIDER_REJECTED','DELIVERY_UNCERTAIN','CHAT_UNAVAILABLE','TELEGRAM_NOT_CONNECTED','INVALID_STATE','RETRY_EXHAUSTED','DELIVERY_EXPIRED','CONFIGURATION_FAILED')", name="ck_reminders_error"),
    )
    op.create_index("ix_reminders_pending", "application_reminders", ["delivery_state", "next_attempt_at"])
    op.create_index("ix_reminders_lease", "application_reminders", ["delivery_state", "lease_until"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM application_reminders LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while reminder data exists")
    op.drop_table("application_reminders")
