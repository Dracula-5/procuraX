"""Add auditable delegation records for named approval steps."""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLE = "approval_delegations"
TENANT_PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("delegator_id", sa.Uuid(), nullable=False),
        sa.Column("delegate_id", sa.Uuid(), nullable=False),
        sa.Column("delegated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.ForeignKeyConstraint(["delegator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["delegate_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "approval_id", name="uq_approval_delegations_org_id_approval_id"),
    )
    op.create_index("ix_approval_delegations_org_id", TABLE, ["org_id"])
    op.create_index("ix_approval_delegations_approval_id", TABLE, ["approval_id"])
    op.create_index("ix_approval_delegations_delegator_id", TABLE, ["delegator_id"])
    op.create_index("ix_approval_delegations_delegate_id", TABLE, ["delegate_id"])
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Invalid database role name: {role!r}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON approval_delegations TO {role}")  # noqa: S608


def downgrade() -> None:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Invalid database role name: {role!r}")
    op.execute(f"REVOKE ALL ON approval_delegations FROM {role}")  # noqa: S608
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON approval_delegations")
    op.drop_table(TABLE)
