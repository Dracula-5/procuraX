"""Quote line prices, award decision context and qualified-invoice registration numbers."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Databases migrated through an early 0009/0010 kept a double-prefixed check name.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'procurement_knowledge_documents'::regclass
                  AND conname = 'ck_procurement_knowledge_documents_ck_procurement_knowl_04ac'
            ) THEN
                ALTER TABLE procurement_knowledge_documents
                    RENAME CONSTRAINT ck_procurement_knowledge_documents_ck_procurement_knowl_04ac
                    TO ck_procurement_knowledge_documents_positive_version;
            END IF;
        END $$;
        """
    )
    op.add_column(
        "vendor_quotes",
        sa.Column(
            "line_prices",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "vendor_quotes", sa.Column("award_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("invoices", sa.Column("registration_number", sa.String(length=14), nullable=True))
    op.create_check_constraint(
        "registration_number_format",
        "invoices",
        "registration_number IS NULL OR registration_number ~ '^T[0-9]{13}$'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_invoices_registration_number_format"), "invoices", type_="check")
    op.drop_column("invoices", "registration_number")
    op.drop_column("vendor_quotes", "award_context")
    op.drop_column("vendor_quotes", "line_prices")
