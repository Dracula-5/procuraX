"""Add tenant-scoped, versioned procurement knowledge documents."""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLE = "procurement_knowledge_documents"
TENANT_PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_reference", sa.String(500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "title", "version", name="uq_procurement_knowledge_documents_org_id_title_version"
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_procurement_knowledge_documents_positive_version")),
    )
    op.create_index(f"ix_{TABLE}_org_id", TABLE, ["org_id"])
    op.create_index(
        "ix_procurement_knowledge_documents_org_active",
        TABLE,
        ["org_id", "is_active"],
        postgresql_where=sa.text("is_active"),
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Invalid database role name: {role!r}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {role}")  # noqa: S608


def downgrade() -> None:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Invalid database role name: {role!r}")
    op.execute(f"REVOKE ALL ON {TABLE} FROM {role}")  # noqa: S608
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.drop_table(TABLE)
