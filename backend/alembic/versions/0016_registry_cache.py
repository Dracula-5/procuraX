"""Cache of the NTA qualified-invoice issuer registry (public data, shared across tenants).

No org_id and no row-level security: the rows are the NTA's published data about registration numbers,
not tenant data. The runtime role may SELECT, INSERT and UPDATE (refresh); it cannot delete.
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "qualified_invoice_registry_cache",
        sa.Column("registration_number", sa.String(length=14), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("registered_on", sa.Date(), nullable=True),
        sa.Column("revoked_on", sa.Date(), nullable=True),
        sa.Column("expired_on", sa.Date(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("registration_number", name="pk_qualified_invoice_registry_cache"),
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, UPDATE ON qualified_invoice_registry_cache TO {role}; END IF; END $$;"
    )


def downgrade() -> None:
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON qualified_invoice_registry_cache FROM {role}; END IF; END $$;"
    )
    op.drop_table("qualified_invoice_registry_cache")
