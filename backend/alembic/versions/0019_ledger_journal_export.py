"""ERP journal export: GL account mapping, export batches and per-document export markers.

gl_account_mappings and journal_exports are tenant tables: org_id, ENABLE + FORCE RLS with 0001's
tenant_isolation predicate. Runtime role: mappings SELECT, INSERT, UPDATE, DELETE (configuration);
journal_exports SELECT, INSERT only (an export batch, file included, is a record: never edited or
removed; the file is kept in the row so it can be downloaded again without object storage).

organizations.ledger_settings holds the control accounts (payable, input tax, bank, default expense).
invoices / payments.journal_export_id mark what has been exported, so nothing is exported twice.
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TENANT_PREDICATE = (
    "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
    "OR current_setting('app.rls_bypass', true) = 'on'"
)
GRANTS = {"gl_account_mappings": "SELECT, INSERT, UPDATE, DELETE", "journal_exports": "SELECT, INSERT"}


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.add_column("organizations", sa.Column("ledger_settings", postgresql.JSONB(), nullable=True))
    op.create_table(
        "gl_account_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("cost_center_id", sa.Uuid(), nullable=True),
        sa.Column("expense_account", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IS NOT NULL OR cost_center_id IS NOT NULL",
            name=op.f("ck_gl_account_mappings_has_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_gl_account_mappings_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cost_center_id"], ["cost_centers.id"], name="fk_gl_account_mappings_cost_center_id_cost_centers"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_account_mappings"),
        sa.UniqueConstraint(
            "org_id",
            "category",
            "cost_center_id",
            name="uq_gl_account_mappings_org_id_category_cost_center_id",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_gl_account_mappings_org_id", "gl_account_mappings", ["org_id"])
    op.create_table(
        "journal_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=40), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("invoice_count", sa.Integer(), nullable=False),
        sa.Column("payment_count", sa.Integer(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_journal_exports_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name="fk_journal_exports_created_by_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_journal_exports"),
    )
    op.create_index("ix_journal_exports_org_id", "journal_exports", ["org_id"])
    for table in GRANTS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )
    role = _role()
    for table, privileges in GRANTS.items():
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
            f"GRANT {privileges} ON {table} TO {role}; END IF; END $$;"
        )
    for table in ("invoices", "payments"):
        op.add_column(table, sa.Column("journal_export_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_journal_export_id_journal_exports",
            table,
            "journal_exports",
            ["journal_export_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_journal_export_id", table, ["journal_export_id"])


def downgrade() -> None:
    for table in ("payments", "invoices"):
        op.drop_index(f"ix_{table}_journal_export_id", table_name=table)
        op.drop_constraint(f"fk_{table}_journal_export_id_journal_exports", table, type_="foreignkey")
        op.drop_column(table, "journal_export_id")
    role = _role()
    for table in GRANTS:
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
            f"REVOKE ALL ON {table} FROM {role}; END IF; END $$;"
        )
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("journal_exports")
    op.drop_table("gl_account_mappings")
    op.drop_column("organizations", "ledger_settings")
