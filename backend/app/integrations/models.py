"""Cached answers from external public registries.

`qualified_invoice_registry_cache` holds the NTA's *public* qualified-invoice issuer data, keyed by
registration number. It contains no tenant data, so it is shared across tenants and has no org_id or
row-level security; the runtime role may read and refresh it but not delete from it.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.qualified_invoice import RegistryRecord


class QualifiedInvoiceRegistryEntry(Base):
    __tablename__ = "qualified_invoice_registry_cache"

    registration_number: Mapped[str] = mapped_column(String(14), primary_key=True)
    found: Mapped[bool] = mapped_column(Boolean)
    name: Mapped[str | None] = mapped_column(String(300))
    registered_on: Mapped[date | None] = mapped_column(Date)
    revoked_on: Mapped[date | None] = mapped_column(Date)
    expired_on: Mapped[date | None] = mapped_column(Date)
    payload: Mapped[dict | None] = mapped_column(JSONB)  # the latest announcement as published
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_record(self) -> RegistryRecord:
        return RegistryRecord(
            registration_number=self.registration_number,
            found=self.found,
            fetched_at=self.fetched_at,
            name=self.name,
            registered_on=self.registered_on,
            revoked_on=self.revoked_on,
            expired_on=self.expired_on,
        )
