"""Supplier contracts with a lifecycle (draft → active → expired / terminated), value cap and document.

Tenant table: org_id, ENABLE + FORCE RLS with 0001's tenant_isolation predicate (the worker's expiry
and reminder sweep runs across tenants with the explicit bypass). Runtime role: SELECT, INSERT, UPDATE.
vendors.contract_status stays as the fallback for vendors without contract records.
purchase_requests.contract_id records the contract that covered a request at submission (cap usage).
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0017"
down_revision = "0016"
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
        "contracts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("value_cap", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("terminated_reason", sa.Text(), nullable=True),
        sa.Column("document_object_key", sa.String(length=300), nullable=True),
        sa.Column("document_sha256", sa.String(length=64), nullable=True),
        sa.Column("document_filename", sa.String(length=255), nullable=True),
        sa.Column("document_mime_type", sa.String(length=60), nullable=True),
        sa.Column("document_scan_status", sa.String(length=16), nullable=True),
        sa.Column("expiry_reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'expired', 'terminated')", name=op.f("ck_contracts_valid_status")
        ),
        sa.CheckConstraint("ends_on >= starts_on", name=op.f("ck_contracts_dates_ordered")),
        sa.CheckConstraint("value_cap IS NULL OR value_cap > 0", name=op.f("ck_contracts_positive_cap")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_contracts_org_id_organizations", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], name="fk_contracts_vendor_id_vendors"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name="fk_contracts_created_by_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_contracts"),
    )
    op.create_index("ix_contracts_org_id", "contracts", ["org_id"])
    op.create_index("ix_contracts_vendor_id", "contracts", ["vendor_id"])
    op.create_index("ix_contracts_org_vendor_status", "contracts", ["org_id", "vendor_id", "status"])
    op.execute("ALTER TABLE contracts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contracts FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON contracts USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, UPDATE ON contracts TO {role}; END IF; END $$;"
    )
    op.add_column("purchase_requests", sa.Column("contract_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_purchase_requests_contract_id_contracts",
        "purchase_requests",
        "contracts",
        ["contract_id"],
        ["id"],
    )
    op.create_index("ix_purchase_requests_contract_id", "purchase_requests", ["contract_id"])


def downgrade() -> None:
    op.drop_index("ix_purchase_requests_contract_id", table_name="purchase_requests")
    op.drop_constraint("fk_purchase_requests_contract_id_contracts", "purchase_requests", type_="foreignkey")
    op.drop_column("purchase_requests", "contract_id")
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON contracts FROM {role}; END IF; END $$;"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON contracts")
    op.drop_table("contracts")
