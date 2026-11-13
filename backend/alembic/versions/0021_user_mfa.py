"""TOTP multi-factor authentication on users.

Secrets are encrypted with PROCURAX_DATA_ENCRYPTION_KEY (Fernet); recovery codes are stored as HMACs;
mfa_last_step stops a code from being used twice. `users` already grants SELECT/INSERT/UPDATE to the
runtime role (0001), so new columns need no grant.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_pending_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("mfa_last_step", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("mfa_recovery_hashes", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    for column in (
        "mfa_recovery_hashes",
        "mfa_last_step",
        "mfa_enabled_at",
        "mfa_pending_secret_encrypted",
        "mfa_secret_encrypted",
    ):
        op.drop_column("users", column)
