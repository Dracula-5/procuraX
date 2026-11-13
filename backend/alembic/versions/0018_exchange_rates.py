"""Multi-currency requests: tenant exchange rates and a rate snapshot on each purchase request.

exchange_rates is a tenant table: org_id, ENABLE + FORCE RLS with 0001's tenant_isolation predicate.
Runtime role: SELECT, INSERT, UPDATE (a manual rate for a day can be corrected; rows are not deleted,
and a request keeps the rate it was submitted with).

purchase_requests gains fx_rate / fx_rate_on / fx_source / base_amount. base_amount is what policy,
budgets and analytics add up; existing rows are all in the base currency, so it is backfilled from
estimated_total.
"""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TENANT_PREDICATE = (
    "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
    "OR current_setting('app.rls_bypass', true) = 'on'"
)


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("valid_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rate > 0", name=op.f("ck_exchange_rates_positive_rate")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_exchange_rates_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name="fk_exchange_rates_created_by_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exchange_rates"),
        sa.UniqueConstraint(
            "org_id",
            "currency",
            "valid_on",
            "source",
            name="uq_exchange_rates_org_id_currency_valid_on_source",
        ),
    )
    op.create_index("ix_exchange_rates_org_id", "exchange_rates", ["org_id"])
    op.create_index("ix_exchange_rates_lookup", "exchange_rates", ["org_id", "currency", "valid_on"])
    op.execute("ALTER TABLE exchange_rates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE exchange_rates FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON exchange_rates "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, UPDATE ON exchange_rates TO {role}; END IF; END $$;"
    )

    op.add_column(
        "purchase_requests", sa.Column("base_amount", sa.Numeric(precision=18, scale=2), nullable=True)
    )
    op.add_column("purchase_requests", sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), nullable=True))
    op.add_column("purchase_requests", sa.Column("fx_rate_on", sa.Date(), nullable=True))
    op.add_column("purchase_requests", sa.Column("fx_source", sa.String(length=40), nullable=True))
    # Every existing request is in its tenant's base currency. The migration owner may not be a
    # superuser, so take the explicit bypass for this transaction before touching tenant rows.
    op.execute("SELECT set_config('app.rls_bypass', 'on', true)")
    op.execute("UPDATE purchase_requests SET base_amount = estimated_total WHERE base_amount IS NULL")


def downgrade() -> None:
    op.drop_column("purchase_requests", "fx_source")
    op.drop_column("purchase_requests", "fx_rate_on")
    op.drop_column("purchase_requests", "fx_rate")
    op.drop_column("purchase_requests", "base_amount")
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON exchange_rates FROM {role}; END IF; END $$;"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON exchange_rates")
    op.drop_table("exchange_rates")
