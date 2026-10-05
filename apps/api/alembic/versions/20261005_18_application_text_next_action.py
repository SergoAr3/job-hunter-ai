"""Allow user-authored next actions without a date."""
from alembic import op
import sqlalchemy as sa

revision = "20261005_18"
down_revision = "20261003_17"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_applications_next_action_block"


def _replace_constraint(expression: str) -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT, expression)


def upgrade() -> None:
    _replace_constraint("next_action_due_on IS NULL OR next_action IS NOT NULL")


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM applications WHERE next_action IS NOT NULL "
        "AND next_action_due_on IS NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Cannot downgrade while undated next actions exist")
    _replace_constraint(
        "(next_action IS NULL AND next_action_due_on IS NULL) OR "
        "(next_action IS NOT NULL AND next_action_due_on IS NOT NULL)"
    )
