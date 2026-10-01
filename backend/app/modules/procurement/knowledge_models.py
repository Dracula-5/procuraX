"""Tenant-owned source documents for extractive procurement knowledge retrieval."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class ProcurementKnowledgeDocument(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "procurement_knowledge_documents"
    __table_args__ = (
        UniqueConstraint("org_id", "title", "version"),
        CheckConstraint("version > 0", name="positive_version"),
        Index(
            "ix_procurement_knowledge_documents_org_active",
            "org_id",
            "is_active",
            postgresql_where=text("is_active"),
        ),
    )

    title: Mapped[str] = mapped_column(String(200))
    source_reference: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
