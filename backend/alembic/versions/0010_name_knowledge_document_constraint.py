"""Align the knowledge document key name with the metadata naming convention.

Databases created by an early revision of 0009 have the old key name and a double-prefixed check
constraint; a fresh 0009 already creates both with the conventional names. The renames are
therefore conditional, so fresh and previously migrated databases converge on the same schema.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLE = "procurement_knowledge_documents"
LEGACY_CHECK = "ck_procurement_knowledge_documents_ck_procurement_knowl_04ac"


def _rename_if_exists(old: str, new: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{TABLE}'::regclass AND conname = '{old}'
            ) THEN
                ALTER TABLE {TABLE} RENAME CONSTRAINT {old} TO {new};
            END IF;
        END $$;
        """  # noqa: S608 - module constants only
    )


def upgrade() -> None:
    _rename_if_exists(
        "uq_procurement_knowledge_documents_org_title_version",
        "uq_procurement_knowledge_documents_org_id_title_version",
    )
    _rename_if_exists(LEGACY_CHECK, "ck_procurement_knowledge_documents_positive_version")


def downgrade() -> None:
    # Conventional names are valid for revision 0009 as well; nothing to undo.
    pass
