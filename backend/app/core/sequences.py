import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Single atomic statement: the upsert takes a row lock, so concurrent callers in the same
# tenant get distinct numbers without an application-level lock.
_NEXT = text(
    """
    INSERT INTO org_sequences (org_id, name, next_value) VALUES (:org_id, :name, 2)
    ON CONFLICT (org_id, name) DO UPDATE SET next_value = org_sequences.next_value + 1
    RETURNING next_value - 1
    """
)


async def next_value(session: AsyncSession, org_id: uuid.UUID, name: str) -> int:
    result = await session.execute(_NEXT, {"org_id": org_id, "name": name})
    return int(result.scalar_one())


async def next_document_number(session: AsyncSession, org_id: uuid.UUID, prefix: str, year: int) -> str:
    """e.g. PR-2026-000042. Numbering restarts every calendar year."""
    value = await next_value(session, org_id, f"{prefix}-{year}")
    return f"{prefix}-{year}-{value:06d}"
