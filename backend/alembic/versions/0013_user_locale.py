"""Per-user UI language (en / ja). NULL means the user has not chosen; the browser language applies."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `users` already grants SELECT/INSERT/UPDATE to the runtime role (0001); a new column needs nothing more.
    op.add_column("users", sa.Column("locale", sa.String(length=5), nullable=True))
    op.create_check_constraint("valid_locale", "users", "locale IN ('en', 'ja')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_valid_locale"), "users", type_="check")
    op.drop_column("users", "locale")
