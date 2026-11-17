"""pgvector and the knowledge-chunk index for hybrid retrieval.

Creates the `vector` extension (available on the pgvector image used locally and in CI, and on Neon) and
`knowledge_chunks`: tenant table with org_id, ENABLE + FORCE RLS (0001 predicate). Runtime role: SELECT,
INSERT, DELETE (a re-indexed document replaces its chunks). An HNSW index serves cosine distance.
"""

import re

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from app.core.config import get_settings

revision = "0024"
down_revision = "0023"
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
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(256), nullable=False),
        sa.Column("embedding_model", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_knowledge_chunks_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["procurement_knowledge_documents.id"],
            name="fk_knowledge_chunks_document_id_procurement_knowledge_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunks"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunks_document_id_chunk_index"),
    )
    op.create_index("ix_knowledge_chunks_org_id", "knowledge_chunks", ["org_id"])
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON knowledge_chunks USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT, DELETE ON knowledge_chunks TO {role}; END IF; END $$;"
    )


def downgrade() -> None:
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON knowledge_chunks FROM {role}; END IF; END $$;"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.execute("DROP EXTENSION IF EXISTS vector")
