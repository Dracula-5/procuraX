"""Sign-in sessions: rotating refresh tokens (hashes only) grouped in families (ADR-019).

Tenant table: org_id, ENABLE + FORCE RLS with 0001's tenant_isolation predicate. The refresh endpoint
runs before any tenant is known, so it looks a token up through the explicit bypass (like sign-in).
Runtime role: SELECT, INSERT, UPDATE; rows are revoked, never deleted.
"""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TENANT_PREDICATE = (
    "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
    "OR current_setting('app.rls_bypass', true) = 'on'"
)


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=40), nullable=True),
        sa.Column("user_agent", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_auth_sessions_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_org_id", "auth_sessions", ["org_id"])
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["org_id", "user_id", "revoked_at"])
    op.execute("ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auth_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON auth_sessions USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, UPDATE ON auth_sessions TO {role}; END IF; END $$;"
    )


def downgrade() -> None:
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON auth_sessions FROM {role}; END IF; END $$;"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON auth_sessions")
    op.drop_table("auth_sessions")
