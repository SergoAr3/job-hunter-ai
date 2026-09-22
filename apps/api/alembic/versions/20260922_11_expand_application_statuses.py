"""Expand application statuses for explicit user-recorded outcomes."""

from alembic import op
import sqlalchemy as sa


revision = "20260922_11"
down_revision = "20260912_10"
branch_labels = None
depends_on = None

_OLD_STATUSES = "'saved', 'applied', 'interview', 'offer', 'rejected'"
_NEW_STATUSES = (
    "'saved', 'applied', 'recruiter_response', 'interview', 'offer', "
    "'hired', 'withdrawn', 'rejected'"
)
_NEW_ONLY_STATUSES = "'recruiter_response', 'hired', 'withdrawn'"


def _replace_status_constraint(
    table: str,
    constraint: str,
    statuses: str,
    *,
    existing_length: int,
    new_length: int,
) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.alter_column(
            "status", existing_type=sa.String(existing_length), type_=sa.String(new_length)
        )
        batch_op.create_check_constraint(constraint, f"status IN ({statuses})")


def upgrade() -> None:
    _replace_status_constraint(
        "applications", "ck_applications_status", _NEW_STATUSES,
        existing_length=16, new_length=32,
    )
    _replace_status_constraint(
        "application_status_history", "ck_application_status_history_status", _NEW_STATUSES,
        existing_length=16, new_length=32,
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("applications", "application_status_history"):
        has_new_status = bind.execute(sa.text(
            f"SELECT 1 FROM {table} WHERE status IN ({_NEW_ONLY_STATUSES}) LIMIT 1"
        )).first()
        if has_new_status is not None:
            raise RuntimeError(
                "Cannot downgrade application statuses while explicit outcome data exists"
            )
    _replace_status_constraint(
        "application_status_history", "ck_application_status_history_status", _OLD_STATUSES,
        existing_length=32, new_length=16,
    )
    _replace_status_constraint(
        "applications", "ck_applications_status", _OLD_STATUSES,
        existing_length=32, new_length=16,
    )
