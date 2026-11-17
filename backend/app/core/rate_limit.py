"""Failed-attempt throttling: the interface and an in-process implementation.

The API uses `app.modules.identity.throttle.PostgresThrottle`, which every replica shares. The in-process
`MemoryThrottle` is for single-process tools and tests. Each key has its own limit inside one window.
"""

import hashlib
import time
from collections import deque
from typing import Protocol

from app.core.errors import RateLimitedError

_MAX_KEYS = 50_000


def refuse(retry_after_seconds: int) -> RateLimitedError:
    return RateLimitedError(
        "Too many failed attempts; try again later",
        details={"retry_after_seconds": max(1, retry_after_seconds)},
    )


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class Throttle(Protocol):
    window_seconds: int

    async def check(self, *keys_and_limits: tuple[str, int]) -> None: ...

    async def record_failure(self, *keys: str) -> None: ...

    async def reset(self, *keys: str) -> None: ...


class MemoryThrottle:
    """Sliding window per key, in this process only."""

    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str, now: float) -> deque[float]:
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        while entries and now - entries[0] >= self.window_seconds:
            entries.popleft()
        if not entries:
            del self._failures[key]
        return entries

    async def check(self, *keys_and_limits: tuple[str, int]) -> None:
        now = time.monotonic()
        for key, limit in keys_and_limits:
            entries = self._recent(key, now)
            if len(entries) >= limit:
                raise refuse(int(self.window_seconds - (now - entries[0])) + 1)

    async def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        if len(self._failures) >= _MAX_KEYS:
            # Bound memory: drop the key whose newest failure is oldest.
            stalest = min(self._failures, key=lambda k: self._failures[k][-1])
            del self._failures[stalest]
        for key in keys:
            self._failures.setdefault(key, deque()).append(now)

    async def reset(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)
