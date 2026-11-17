"""AI records: every model output (ai_predictions) and the vector index of knowledge text (knowledge_chunks)."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, UUIDPkMixin, utcnow

EMBEDDING_DIMENSIONS = 256


class AIPrediction(UUIDPkMixin, TenantMixin, Base):
    """One output of a model, with what produced it and from which input. Insert-only."""

    __tablename__ = "ai_predictions"
    __table_args__ = (Index("ix_ai_predictions_input", "org_id", "input_ref", "kind"),)

    kind: Mapped[str] = mapped_column(String(40))  # advisory_flags, knowledge_answer, sql_draft, ...
    model: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(120))
    input_ref: Mapped[str] = mapped_column(String(120))  # e.g. purchase_request:<id>, question:<sha256>
    input_sha256: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSONB)
    latency_ms: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(UUIDPkMixin, TenantMixin, Base):
    """A passage of a tenant knowledge document with its embedding (pgvector) for hybrid retrieval."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("procurement_knowledge_documents.id"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
