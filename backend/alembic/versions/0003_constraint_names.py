"""Align generated constraint and index names with ORM metadata."""

from alembic import op

revision = "0003"
down_revision = "0002_purchase_to_pay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE purchase_orders RENAME CONSTRAINT uq_purchase_orders_org_number "
        "TO uq_purchase_orders_org_id_number"
    )
    op.execute(
        "ALTER TABLE purchase_orders RENAME CONSTRAINT uq_purchase_orders_org_purchase_request_id "
        "TO uq_purchase_orders_org_id_purchase_request_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE purchase_orders RENAME CONSTRAINT uq_purchase_orders_org_id_number "
        "TO uq_purchase_orders_org_number"
    )
    op.execute(
        "ALTER TABLE purchase_orders RENAME CONSTRAINT uq_purchase_orders_org_id_purchase_request_id "
        "TO uq_purchase_orders_org_purchase_request_id"
    )
