"""Database engine, session factory and tenant context.

Tenant isolation is enforced twice:

1. Application layer: every query is built from the authenticated principal's org_id.
2. Database layer: PostgreSQL Row-Level Security (RLS). Each transaction carries the tenant
   in a transaction-local setting (``app.org_id``); RLS policies compare every row's
   ``org_id`` with it. If the setting is missing, the policies match nothing, so a
   forgotten WHERE clause returns zero rows rather than another tenant's data.
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, MetaData, Uuid, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    # Python-side defaults: values are known without a round-trip, which matters under
    # asyncio where an implicit refresh of an expired attribute is not allowed.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TenantMixin:
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True, nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _json_default(value: object) -> str:
    # JSONB columns (audit changes, policy snapshots) may carry these; store them as strings
    # (Decimal as a string keeps money exact).
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(value: object) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            json_serializer=json_dumps,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


_SET_CONTEXT_SQL = text(
    "SELECT set_config('app.org_id', :org_id, true), set_config('app.rls_bypass', :bypass, true)"
)


def _context_params(session: Session) -> dict[str, str]:
    org_id = session.info.get("org_id")
    return {
        "org_id": str(org_id) if org_id else "",
        "bypass": "on" if session.info.get("rls_bypass") else "off",
    }


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:  # noqa: ANN001
    # Runs at the start of every transaction, so the tenant context is re-applied after
    # each commit and never leaks between pooled connections (set_config(..., true) is
    # transaction-local).
    connection.execute(_SET_CONTEXT_SQL, _context_params(session))


async def bind_tenant(session: AsyncSession, org_id: uuid.UUID | None) -> None:
    """Scope the session to one organisation (applies to the current and future transactions)."""
    session.info["org_id"] = org_id
    session.info["rls_bypass"] = False
    if session.in_transaction():
        await session.execute(_SET_CONTEXT_SQL, _context_params(session.sync_session))


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Write endpoints commit explicitly; anything uncommitted is rolled back."""
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def system_session() -> AsyncSession:
    """A session that bypasses RLS.

    Only for the narrow cases where no tenant is known yet: login (lookup by e-mail),
    invitation acceptance (lookup by token), and platform-level administration.
    Every use is deliberate and reviewed; business-data endpoints never use it.
    """
    session = get_sessionmaker()()
    session.info["rls_bypass"] = True
    return session
