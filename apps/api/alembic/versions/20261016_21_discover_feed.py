"""Short-lived, public source evidence for Discover continuation."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_21"
down_revision = "20261009_20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discover_feed_chains",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("creator_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("identity_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("page_count", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("state_count BETWEEN 1 AND 64 AND page_count BETWEEN 0 AND 32", name="ck_feed_chain_capacity"),
        sa.CheckConstraint("expires_at > created_at", name="ck_feed_chain_expiry"),
    )
    op.create_index("ix_feed_chain_expiry", "discover_feed_chains", ["expires_at"])
    op.create_index("ix_feed_chain_creator_expiry", "discover_feed_chains", ["creator_id", "expires_at"])
    op.create_table(
        "discover_feed_states",
        sa.Column("token", sa.String(32), primary_key=True),
        sa.Column("chain_id", sa.String(32), sa.ForeignKey("discover_feed_chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("result_state", sa.String(16)),
        sa.Column("prev_token", sa.String(32), sa.ForeignKey("discover_feed_states.token", ondelete="SET NULL")),
        sa.Column("next_token", sa.String(32), sa.ForeignKey("discover_feed_states.token", ondelete="SET NULL")),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('ready','claimed','complete')", name="ck_feed_state_status"),
        sa.CheckConstraint("(status = 'claimed' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (status <> 'claimed' AND lease_token IS NULL AND lease_until IS NULL)", name="ck_feed_state_lease"),
        sa.CheckConstraint("(status = 'complete' AND result_state IS NOT NULL AND result_state IN ('page','paused','end')) OR (status <> 'complete' AND result_state IS NULL)", name="ck_feed_state_result"),
        sa.CheckConstraint("length(payload) <= 262144", name="ck_feed_state_payload"),
    )
    op.create_index("ix_feed_state_chain", "discover_feed_states", ["chain_id"])
    op.create_index("ix_feed_state_prev", "discover_feed_states", ["prev_token"])
    op.create_index("ix_feed_state_next", "discover_feed_states", ["next_token"])


def downgrade():
    op.drop_table("discover_feed_states")
    op.drop_table("discover_feed_chains")
