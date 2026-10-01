"""Award sourcing quotes and retain line-level receipt and invoice review data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_quotes", sa.Column("is_awarded", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("vendor_quotes", sa.Column("awarded_by_id", sa.Uuid(), nullable=True))
    op.add_column("vendor_quotes", sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_vendor_quotes_awarded_by_id_users", "vendor_quotes", "users", ["awarded_by_id"], ["id"]
    )
    op.create_index(
        "uq_vendor_quotes_awarded_per_request",
        "vendor_quotes",
        ["org_id", "purchase_request_id"],
        unique=True,
        postgresql_where=sa.text("is_awarded"),
    )

    op.add_column("purchase_orders", sa.Column("awarded_quote_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_purchase_orders_awarded_quote_id_vendor_quotes",
        "purchase_orders",
        "vendor_quotes",
        ["awarded_quote_id"],
        ["id"],
    )

    op.add_column("goods_receipts", sa.Column("purchase_order_item_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_goods_receipts_purchase_order_item_id",
        "goods_receipts",
        "purchase_order_items",
        ["purchase_order_item_id"],
        ["id"],
    )
    op.create_index(
        "ix_goods_receipts_org_po_item",
        "goods_receipts",
        ["org_id", "purchase_order_id", "purchase_order_item_id"],
    )

    op.add_column("invoices", sa.Column("reviewed_by_id", sa.Uuid(), nullable=True))
    op.add_column("invoices", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("review_comment", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("submitted_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_invoices_reviewed_by_id_users", "invoices", "users", ["reviewed_by_id"], ["id"])
    op.create_foreign_key(
        "fk_invoices_submitted_by_id_users", "invoices", "users", ["submitted_by_id"], ["id"]
    )
    op.drop_constraint(op.f("ck_invoices_valid_match_status"), "invoices", type_="check")
    op.create_check_constraint(
        "valid_match_status",
        "invoices",
        "match_status IN ('matched', 'partially_matched', 'mismatch', 'duplicate', 'requires_review', 'exception_approved', 'exception_rejected')",
    )
    op.add_column(
        "invoices",
        sa.Column(
            "line_match_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("invoices", "line_match_details")
    op.drop_constraint(op.f("ck_invoices_valid_match_status"), "invoices", type_="check")
    op.create_check_constraint(
        "valid_match_status",
        "invoices",
        "match_status IN ('matched', 'partially_matched', 'mismatch', 'duplicate', 'requires_review')",
    )
    op.drop_constraint("fk_invoices_reviewed_by_id_users", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoices_submitted_by_id_users", "invoices", type_="foreignkey")
    op.drop_column("invoices", "submitted_by_id")
    op.drop_column("invoices", "review_comment")
    op.drop_column("invoices", "reviewed_at")
    op.drop_column("invoices", "reviewed_by_id")
    op.drop_index("ix_goods_receipts_org_po_item", table_name="goods_receipts")
    op.drop_constraint("fk_goods_receipts_purchase_order_item_id", "goods_receipts", type_="foreignkey")
    op.drop_column("goods_receipts", "purchase_order_item_id")
    op.drop_constraint(
        "fk_purchase_orders_awarded_quote_id_vendor_quotes", "purchase_orders", type_="foreignkey"
    )
    op.drop_column("purchase_orders", "awarded_quote_id")
    op.drop_index("uq_vendor_quotes_awarded_per_request", table_name="vendor_quotes")
    op.drop_constraint("fk_vendor_quotes_awarded_by_id_users", "vendor_quotes", type_="foreignkey")
    op.drop_column("vendor_quotes", "awarded_at")
    op.drop_column("vendor_quotes", "awarded_by_id")
    op.drop_column("vendor_quotes", "is_awarded")
