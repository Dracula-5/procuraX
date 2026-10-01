"""Purchase orders, goods receipts, invoices and tenant RLS."""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0002_purchase_to_pay"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("purchase_orders", "purchase_order_items", "goods_receipts", "invoices")
TENANT_PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("purchase_request_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("expected_delivery", sa.Date(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total >= 0", name="ck_purchase_orders_total_non_negative"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_request_id"], ["purchase_requests.id"]),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "number", name="uq_purchase_orders_org_number"),
        sa.UniqueConstraint(
            "org_id", "purchase_request_id", name="uq_purchase_orders_org_purchase_request_id"
        ),
    )
    op.create_index("ix_purchase_orders_org_id", "purchase_orders", ["org_id"])
    op.create_index("ix_purchase_orders_org_status", "purchase_orders", ["org_id", "status"])
    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(18, 2), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "purchase_order_id", "line_no", name="uq_purchase_order_items_purchase_order_id_line_no"
        ),
        sa.CheckConstraint(
            "quantity > 0 AND unit_price >= 0", name="ck_purchase_order_items_valid_quantity_price"
        ),
    )
    op.create_index("ix_purchase_order_items_org_id", "purchase_order_items", ["org_id"])
    op.create_index(
        "ix_purchase_order_items_purchase_order_id", "purchase_order_items", ["purchase_order_id"]
    )
    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("received_by_id", sa.Uuid(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("damaged_quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
        sa.ForeignKeyConstraint(["received_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "quantity > 0 AND damaged_quantity >= 0 AND damaged_quantity <= quantity",
            name="ck_goods_receipts_valid_quantities",
        ),
    )
    op.create_index("ix_goods_receipts_org_id", "goods_receipts", ["org_id"])
    op.create_index("ix_goods_receipts_org_po", "goods_receipts", ["org_id", "purchase_order_id"])
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax", sa.Numeric(18, 2), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("match_status", sa.String(24), nullable=False),
        sa.Column("match_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "quantity > 0 AND total >= 0 AND subtotal >= 0 AND tax >= 0",
            name="ck_invoices_valid_invoice_amounts",
        ),
    )
    op.create_index("ix_invoices_org_id", "invoices", ["org_id"])
    op.create_index("ix_invoices_org_status", "invoices", ["org_id", "match_status"])
    op.create_index(
        "uq_invoices_vendor_number_ci",
        "invoices",
        ["org_id", "vendor_id", sa.text("lower(invoice_number)")],
        unique=True,
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Invalid database role name: {role!r}")
    op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
      GRANT SELECT, INSERT, UPDATE, DELETE ON purchase_orders, purchase_order_items, goods_receipts, invoices TO {role};
    END IF; END $$;""")  # noqa: S608 - role identifier validated above


def downgrade() -> None:
    role = get_settings().app_db_role
    if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        op.execute(
            f"REVOKE ALL ON purchase_orders, purchase_order_items, goods_receipts, invoices FROM {role}"
        )
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
