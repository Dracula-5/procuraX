"""Failed-attempt throttle stored in PostgreSQL, so every API process and replica sees the same counts.

Failures are counted per hashed key in fixed buckets of a tenth of the window; a key is refused while
the failures inside the window reach its limit. Old buckets are pruned as new failures are written.
"""

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.rate_limit import key_hash, refuse
from app.modules.identity.models import AuthThrottle


class PostgresThrottle:
    def __init__(
        self, window_seconds: int, sessionmaker: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self.window_seconds = window_seconds
        self.bucket_seconds = max(1, math.ceil(window_seconds / 10))
        self._sessionmaker = sessionmaker

    def _session(self) -> AsyncSession:
        return (self._sessionmaker or get_sessionmaker())()

    def _bucket(self, now: datetime) -> datetime:
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - epoch % self.bucket_seconds, UTC)

    async def check(self, *keys_and_limits: tuple[str, int]) -> None:
        now = datetime.now(UTC)
        limits = {key_hash(k): limit for k, limit in keys_and_limits}
        async with self._session() as session:
            rows = await session.execute(
                select(AuthThrottle.key_hash, func.sum(AuthThrottle.failures), func.min(AuthThrottle.bucket))
                .where(
                    AuthThrottle.key_hash.in_(limits),
                    AuthThrottle.bucket > now - timedelta(seconds=self.window_seconds),
                )
                .group_by(AuthThrottle.key_hash)
            )
            for digest, failures, oldest in rows:
                if failures >= limits[digest]:
                    window_end = oldest + timedelta(seconds=self.window_seconds + self.bucket_seconds)
                    raise refuse(int((window_end - now).total_seconds()) + 1)

    async def record_failure(self, *keys: str) -> None:
        now = datetime.now(UTC)
        bucket = self._bucket(now)
        async with self._session() as session:
            for key in keys:
                stmt = insert(AuthThrottle).values(key_hash=key_hash(key), bucket=bucket, failures=1)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[AuthThrottle.key_hash, AuthThrottle.bucket],
                        set_={"failures": AuthThrottle.failures + 1},
                    )
                )
            await session.execute(
                delete(AuthThrottle).where(
                    AuthThrottle.bucket < now - timedelta(seconds=2 * self.window_seconds)
                )
            )
            await session.commit()

    async def reset(self, *keys: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(AuthThrottle).where(AuthThrottle.key_hash.in_([key_hash(k) for k in keys]))
            )
            await session.commit()


def limits_for(purpose: str, client: str, account: str) -> tuple[list[tuple[str, int]], list[str], str]:
    """(keys with limits, keys to count a failure against, key to clear on success) for one attempt.

    Per address + account (the main limit), per address (one machine guessing many accounts) and per
    account (many machines guessing one account), the last two at four times the limit.
    """
    limit = get_settings().login_max_failures
    pair, address, account_key = (
        f"{purpose}:{client}:{account}",
        f"{purpose}:{client}",
        f"{purpose}:*:{account}",
    )
    return [(pair, limit), (address, limit * 4), (account_key, limit * 4)], [pair, address, account_key], pair


auth_throttle = PostgresThrottle(get_settings().login_lockout_seconds)
