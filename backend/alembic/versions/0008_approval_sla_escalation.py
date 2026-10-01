"""Track automatic approval SLA escalations."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals", sa.Column("escalation_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("approvals", sa.Column("last_escalated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_approvals_org_pending_due", "approvals", ["org_id", "status", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_approvals_org_pending_due", table_name="approvals")
    op.drop_column("approvals", "last_escalated_at")
    op.drop_column("approvals", "escalation_count")
