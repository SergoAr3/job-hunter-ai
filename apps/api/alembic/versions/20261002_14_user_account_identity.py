"""Allow Telegram and email credentials on the same domain account.

SQLite uses Alembic's technical table rebuild; IDs and all domain FK rows stay
unchanged. env.py disables FK enforcement before the transaction and validates
the graph before commit. PostgreSQL alters users in place.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_14"
down_revision = "20260923_13"
branch_labels = None
depends_on = None


def _check_sqlite_batch_mode() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
            raise RuntimeError(
                "SQLite users rebuild requires foreign_keys=OFF before the "
                "transaction; run through the Alembic environment"
            )


def upgrade() -> None:
    _check_sqlite_batch_mode()
    with op.batch_alter_table("users") as batch:
        batch.alter_column("telegram_id", existing_type=sa.BigInteger(), nullable=True)
        batch.alter_column("first_name", existing_type=sa.String(255), nullable=True)
        batch.add_column(sa.Column("email", sa.String(320)))
        batch.add_column(sa.Column("email_canonical", sa.String(320)))
        batch.add_column(sa.Column("password_hash", sa.Text()))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("display_name", sa.String(255)))
        batch.create_unique_constraint("uq_users_email_canonical", ["email_canonical"])
        batch.create_check_constraint(
            "ck_users_email_credentials_complete",
            "(email IS NULL AND email_canonical IS NULL AND password_hash IS NULL) OR "
            "(email IS NOT NULL AND email_canonical IS NOT NULL AND password_hash IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_users_identity_present",
            "telegram_id IS NOT NULL OR "
            "(email IS NOT NULL AND email_canonical IS NOT NULL AND password_hash IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_users_email_canonical",
            "email IS NULL OR (email_canonical = lower(trim(email)) "
            "AND length(trim(email)) > 0 AND length(trim(password_hash)) > 0)",
        )
        batch.create_check_constraint(
            "ck_users_email_verification_identity",
            "email_verified_at IS NULL OR email IS NOT NULL",
        )
        # SQLite lower() only folds ASCII; PostgreSQL also folds Unicode.
        # Use the same printable-ASCII email policy on both supported engines.
        ascii_check = (
            "email IS NULL OR (instr(email, char(0)) = 0 "
            "AND instr(email_canonical, char(0)) = 0 AND email NOT GLOB '*[^ -~]*')"
            if op.get_bind().dialect.name == "sqlite"
            else "email IS NULL OR email !~ '[^ -~]'"
        )
        batch.create_check_constraint("ck_users_email_ascii", ascii_check)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Serialize the preflight with writes, so credentials cannot appear
        # between the data-loss check and dropping their columns.
        connection.execute(sa.text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
    incompatible = connection.execute(sa.text(
        "SELECT count(*) FROM users WHERE telegram_id IS NULL OR first_name IS NULL "
        "OR email IS NOT NULL OR email_canonical IS NOT NULL OR password_hash IS NOT NULL "
        "OR email_verified_at IS NOT NULL OR display_name IS NOT NULL"
    )).scalar_one()
    if incompatible:
        raise RuntimeError("Cannot downgrade while account identity data or nullable Telegram metadata exists")

    _check_sqlite_batch_mode()
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_email_ascii", type_="check")
        batch.drop_constraint("ck_users_email_verification_identity", type_="check")
        batch.drop_constraint("ck_users_email_canonical", type_="check")
        batch.drop_constraint("ck_users_identity_present", type_="check")
        batch.drop_constraint("ck_users_email_credentials_complete", type_="check")
        batch.drop_constraint("uq_users_email_canonical", type_="unique")
        batch.drop_column("display_name")
        batch.drop_column("email_verified_at")
        batch.drop_column("password_hash")
        batch.drop_column("email_canonical")
        batch.drop_column("email")
        batch.alter_column("first_name", existing_type=sa.String(255), nullable=False)
        batch.alter_column("telegram_id", existing_type=sa.BigInteger(), nullable=False)
