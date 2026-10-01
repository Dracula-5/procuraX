"""Bank details and Zengin transfer-file export state for approved payments."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendors", sa.Column("bank_account", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "organizations", sa.Column("payment_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("payments", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("exported_by_id", sa.Uuid(), nullable=True))
    op.add_column("payments", sa.Column("export_reference", sa.String(length=40), nullable=True))
    op.add_column("payments", sa.Column("transfer_record", sa.String(length=120), nullable=True))
    op.create_foreign_key("fk_payments_exported_by_id_users", "payments", "users", ["exported_by_id"], ["id"])
    op.create_index("ix_payments_export_reference", "payments", ["export_reference"])
    op.drop_constraint(op.f("ck_payments_valid_status"), "payments", type_="check")
    op.create_check_constraint(
        "valid_status", "payments", "status IN ('pending_approval', 'approved', 'rejected', 'exported')"
    )


def downgrade() -> None:
    op.execute("UPDATE payments SET status = 'approved' WHERE status = 'exported'")
    op.drop_constraint(op.f("ck_payments_valid_status"), "payments", type_="check")
    op.create_check_constraint(
        "valid_status", "payments", "status IN ('pending_approval', 'approved', 'rejected')"
    )
    op.drop_index("ix_payments_export_reference", table_name="payments")
    op.drop_constraint("fk_payments_exported_by_id_users", "payments", type_="foreignkey")
    op.drop_column("payments", "transfer_record")
    op.drop_column("payments", "export_reference")
    op.drop_column("payments", "exported_by_id")
    op.drop_column("payments", "exported_at")
    op.drop_column("organizations", "payment_settings")
    op.drop_column("vendors", "bank_account")
