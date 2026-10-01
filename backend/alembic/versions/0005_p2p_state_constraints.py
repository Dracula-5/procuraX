"""Enforce supported P2P states at the database boundary."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "valid_status",
        "purchase_orders",
        "status IN ('approved', 'sent', 'acknowledged', 'partially_received', 'received', 'closed', 'cancelled')",
    )
    op.create_check_constraint(
        "valid_match_status",
        "invoices",
        "match_status IN ('matched', 'partially_matched', 'mismatch', 'duplicate', 'requires_review')",
    )
    op.create_check_constraint(
        "valid_status",
        "payments",
        "status IN ('pending_approval', 'approved', 'rejected')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_payments_valid_status"), "payments", type_="check")
    op.drop_constraint(op.f("ck_invoices_valid_match_status"), "invoices", type_="check")
    op.drop_constraint(op.f("ck_purchase_orders_valid_status"), "purchase_orders", type_="check")
