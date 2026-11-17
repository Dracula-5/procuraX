"""Stored invoice files (object storage metadata), immutable once written and once linked.

Tenant table with ENABLE + FORCE RLS and 0001's tenant_isolation predicate (the worker scans across
tenants with the explicit bypass). The runtime role may SELECT, INSERT and UPDATE, never DELETE; a
trigger rejects any change to a stored file's identity, any re-link, and deleting a linked file (it
also binds the table owner, so a migration or manual fix cannot quietly rewrite evidence either).
"""

import re

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TENANT_PREDICATE = (
    "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
    "OR current_setting('app.rls_bypass', true) = 'on'"
)

IMMUTABILITY_FUNCTION = """
CREATE FUNCTION invoice_documents_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    -- Deleting a linked file needs the owner role (the runtime role has no DELETE privilege) and an
    -- explicit, transaction-local purge flag: used by the fictional demo reset and retention purges.
    IF OLD.invoice_id IS NOT NULL AND coalesce(current_setting('app.document_purge', true), '') <> 'on' THEN
      RAISE EXCEPTION 'invoice document % is linked to an invoice and cannot be deleted', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
  END IF;
  IF (NEW.org_id, NEW.object_key, NEW.original_filename, NEW.mime_type, NEW.size_bytes, NEW.sha256,
      NEW.page_count, NEW.uploaded_by_id, NEW.retain_until, NEW.created_at)
     IS DISTINCT FROM
     (OLD.org_id, OLD.object_key, OLD.original_filename, OLD.mime_type, OLD.size_bytes, OLD.sha256,
      OLD.page_count, OLD.uploaded_by_id, OLD.retain_until, OLD.created_at) THEN
    RAISE EXCEPTION 'invoice document % is immutable', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.invoice_id IS NOT NULL AND (NEW.invoice_id, NEW.linked_at, NEW.linked_by_id)
     IS DISTINCT FROM (OLD.invoice_id, OLD.linked_at, OLD.linked_by_id) THEN
    RAISE EXCEPTION 'invoice document % is already linked', OLD.id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;
"""


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "invoice_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=True),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=60), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=False),
        sa.Column("retain_until", sa.Date(), nullable=False),
        sa.Column("scan_status", sa.String(length=16), nullable=False),
        sa.Column("scan_detail", sa.Text(), nullable=True),
        sa.Column("scan_attempts", sa.Integer(), nullable=False),
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scan_status IN ('pending', 'clean', 'infected', 'not_scanned')",
            name=op.f("ck_invoice_documents_valid_scan_status"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_invoice_documents_positive_size")),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_invoice_documents_sha256_hex")),
        sa.CheckConstraint(
            "(invoice_id IS NULL) = (linked_at IS NULL)", name=op.f("ck_invoice_documents_link_consistent")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_invoice_documents_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name="fk_invoice_documents_invoice_id_invoices"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"], ["users.id"], name="fk_invoice_documents_uploaded_by_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_id"], ["users.id"], name="fk_invoice_documents_linked_by_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_documents"),
        sa.UniqueConstraint("object_key", name="uq_invoice_documents_object_key"),
    )
    op.create_index("ix_invoice_documents_org_id", "invoice_documents", ["org_id"])
    op.create_index("ix_invoice_documents_invoice_id", "invoice_documents", ["invoice_id"])
    op.create_index("ix_invoice_documents_scan_due", "invoice_documents", ["scan_status", "next_scan_at"])

    op.execute(IMMUTABILITY_FUNCTION)
    op.execute(
        "CREATE TRIGGER invoice_documents_guard BEFORE UPDATE OR DELETE ON invoice_documents "
        "FOR EACH ROW EXECUTE FUNCTION invoice_documents_guard()"
    )
    op.execute("ALTER TABLE invoice_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE invoice_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON invoice_documents USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, UPDATE ON invoice_documents TO {role}; END IF; END $$;"
    )


def downgrade() -> None:
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON invoice_documents FROM {role}; END IF; END $$;"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON invoice_documents")
    op.execute("DROP TRIGGER IF EXISTS invoice_documents_guard ON invoice_documents")
    op.drop_table("invoice_documents")  # rows go with the table; the objects stay in storage
    op.execute("DROP FUNCTION IF EXISTS invoice_documents_guard()")
