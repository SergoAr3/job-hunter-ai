"""Query-scoped Discover inventory foundation; legacy feed is unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261018_22"
down_revision = "20261016_21"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'discover_inventory_keys',
        sa.Column('id', sa.String(32), primary_key=True, nullable=False),
        sa.Column('identity_digest', sa.String(64), nullable=False),
        sa.Column('canonical_identity', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('length(identity_digest) = 64', name='ck_inventory_key_digest'),
        sa.CheckConstraint('length(canonical_identity) > 0', name='ck_inventory_key_identity'),
        sa.CheckConstraint('updated_at >= created_at', name='ck_inventory_key_time'),
        sa.UniqueConstraint('identity_digest', name='uq_inventory_key_digest'),
    )
    op.create_table(
        'discover_inventory_generations',
        sa.Column('id', sa.String(32), primary_key=True, nullable=False),
        sa.Column('key_id', sa.String(32), sa.ForeignKey('discover_inventory_keys.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('status', sa.String(8), nullable=False, server_default='building'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fresh_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_code', sa.String(64), nullable=True),
        sa.Column('source_page_size', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('next_source_page', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('source_total', sa.Integer(), nullable=True),
        sa.Column('source_exhausted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('pages_committed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('records_examined', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('item_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('payload_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_successful_fetch_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_worked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('page_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_token', sa.String(32), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_cooldown_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_source_error', sa.String(64), nullable=True),
        sa.CheckConstraint('page_attempts >= 0 AND total_retry_count >= 0', name='ck_inventory_generation_attempts'),
        sa.CheckConstraint("status <> 'building' OR (completed_at IS NULL AND failed_at IS NULL AND fresh_until IS NULL AND failure_code IS NULL)", name='ck_inventory_generation_building'),
        sa.CheckConstraint('records_examined BETWEEN 0 AND 10000 AND item_count BETWEEN 0 AND 1000 AND item_count <= records_examined AND payload_bytes BETWEEN 0 AND 16777216', name='ck_inventory_generation_capacity'),
        sa.CheckConstraint("status <> 'failed' OR (failed_at IS NOT NULL AND failure_code IS NOT NULL AND length(trim(failure_code)) > 0 AND completed_at IS NULL AND fresh_until IS NULL AND lease_token IS NULL)", name='ck_inventory_generation_failed'),
        sa.CheckConstraint("(lease_token IS NULL AND lease_until IS NULL) OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND status = 'building')", name='ck_inventory_generation_lease'),
        sa.CheckConstraint('source_page_size = 100 AND next_source_page = pages_committed AND pages_committed BETWEEN 0 AND 101', name='ck_inventory_generation_pages'),
        sa.CheckConstraint("status <> 'ready' OR (source_exhausted AND source_total IS NOT NULL AND completed_at IS NOT NULL AND fresh_until IS NOT NULL AND fresh_until > completed_at AND failed_at IS NULL AND failure_code IS NULL AND lease_token IS NULL)", name='ck_inventory_generation_ready'),
        sa.CheckConstraint("status IN ('building','ready','failed','expired')", name='ck_inventory_generation_status'),
        sa.CheckConstraint('(started_at IS NULL OR started_at >= created_at) AND (completed_at IS NULL OR completed_at >= created_at) AND (failed_at IS NULL OR failed_at >= created_at)', name='ck_inventory_generation_time'),
        sa.CheckConstraint('source_total IS NULL OR (source_total BETWEEN 0 AND 10000 AND records_examined <= source_total)', name='ck_inventory_generation_total'),
    )
    op.create_index('ix_inventory_generation_due', 'discover_inventory_generations', ['status', 'next_attempt_at'])
    op.create_index('ix_inventory_generation_fresh', 'discover_inventory_generations', ['key_id', 'status', 'fresh_until'])
    op.create_index('ix_inventory_generation_lease', 'discover_inventory_generations', ['status', 'lease_until'])
    op.create_index('uq_inventory_building_key', 'discover_inventory_generations', ['key_id'], unique=True, postgresql_where=sa.text("status = 'building'"), sqlite_where=sa.text("status = 'building'"))
    op.create_table(
        'discover_inventory_items',
        sa.Column('generation_id', sa.String(32), sa.ForeignKey('discover_inventory_generations.id', ondelete='RESTRICT'), primary_key=True, nullable=False),
        sa.Column('ordinal', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('source_scope', sa.String(255), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('snapshot', sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column('remote_evidence', sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('length(trim(source)) > 0 AND length(trim(source_scope)) > 0 AND length(trim(external_id)) > 0', name='ck_inventory_item_identity'),
        sa.CheckConstraint('ordinal >= 0', name='ck_inventory_item_ordinal'),
        sa.UniqueConstraint('generation_id', 'source', 'source_scope', 'external_id', name='uq_inventory_item_identity'),
    )
    op.create_table(
        'discover_inventory_views',
        sa.Column('id', sa.String(32), primary_key=True, nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column('generation_id', sa.String(32), sa.ForeignKey('discover_inventory_generations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('page_size', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('creator_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('length(token_digest) = 64', name='ck_inventory_view_digest'),
        sa.CheckConstraint('expires_at > created_at', name='ck_inventory_view_expiry'),
        sa.CheckConstraint('page_size BETWEEN 1 AND 10', name='ck_inventory_view_page_size'),
        sa.UniqueConstraint('token_digest', name='uq_inventory_view_digest'),
    )
    op.create_index('ix_inventory_view_creator_expiry', 'discover_inventory_views', ['creator_user_id', 'expires_at'])
    op.create_index('ix_inventory_view_expiry', 'discover_inventory_views', ['expires_at'])
    op.create_index('ix_inventory_view_generation_expiry', 'discover_inventory_views', ['generation_id', 'expires_at'])


def downgrade():
    op.drop_table('discover_inventory_views')
    op.drop_table('discover_inventory_items')
    op.drop_table('discover_inventory_generations')
    op.drop_table('discover_inventory_keys')
