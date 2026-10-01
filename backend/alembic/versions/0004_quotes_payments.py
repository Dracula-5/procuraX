"""Vendor quote capture and finance-approved payment requests."""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("vendor_quotes", "payments")
TENANT_PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "vendor_quotes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_request_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("delivery_days", sa.Integer(), nullable=False),
        sa.Column("quality_score", sa.Numeric(3, 2), nullable=False),
        sa.Column("contract_compliant", sa.Boolean(), nullable=False),
        sa.Column("quote_valid_until", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount >= 0 AND quality_score BETWEEN 0 AND 5", name="ck_vendor_quotes_valid_quote_score"
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_request_id"], ["purchase_requests.id"]),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vendor_quotes_org_id", "vendor_quotes", ["org_id"])
    op.create_index("ix_vendor_quotes_org_request", "vendor_quotes", ["org_id", "purchase_request_id"])
    op.create_index("ix_vendor_quotes_purchase_request_id", "vendor_quotes", ["purchase_request_id"])
    op.create_index("ix_vendor_quotes_vendor_id", "vendor_quotes", ["vendor_id"])
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("submitted_by_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_payments_amount_non_negative"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "invoice_id", name="uq_payments_org_id_invoice_id"),
    )
    op.create_index("ix_payments_org_id", "payments", ["org_id"])
    op.create_index("ix_payments_org_status", "payments", ["org_id", "status"])
    op.create_index("ix_payments_invoice_id", "payments", ["invoice_id"])
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Invalid database role name: {role!r}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON vendor_quotes, payments TO {role}")  # noqa: S608
    # 0001 seeds the catalogue from the current code, so on a fresh database these rows
    # already exist; the inserts only matter for databases first migrated before 0004.
    op.execute(
        "INSERT INTO permissions (key, description) VALUES "
        "('payment:approve', 'Approve or reject payment requests') ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_key, permission_key) "
        "VALUES ('finance_manager', 'payment:approve') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_key = 'payment:approve'")
    op.execute("DELETE FROM permissions WHERE key = 'payment:approve'")
    role = get_settings().app_db_role
    if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        op.execute(f"REVOKE ALL ON vendor_quotes, payments FROM {role}")  # noqa: S608
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
