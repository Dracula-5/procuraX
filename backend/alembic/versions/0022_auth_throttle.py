"""Failed sign-in attempt counters shared by all API processes (replaces the in-process throttle).

Not tenant data and holds no personal data in clear: keys are SHA-256 hashes, so no org_id and no RLS
(like the qualified-invoice registry cache). Runtime role: SELECT, INSERT, UPDATE, DELETE (old buckets are
pruned as new failures are recorded).
"""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "auth_throttle",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key_hash", "bucket", name="pk_auth_throttle"),
    )
    op.create_index("ix_auth_throttle_bucket", "auth_throttle", ["bucket"])
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON auth_throttle TO {role}; END IF; END $$;"
    )


def downgrade() -> None:
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON auth_throttle FROM {role}; END IF; END $$;"
    )
    op.drop_table("auth_throttle")
