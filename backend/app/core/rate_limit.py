"""In-process failed-attempt throttle (sliding window).

Scope: a single API process. Behind several replicas or a proxy, enforce the same policy at the
gateway or with a shared store; client addresses here are the direct peer address.
"""

import time
from collections import deque

from app.core.errors import RateLimitedError

_MAX_KEYS = 50_000


class FailureThrottle:
    def __init__(self, max_failures: int, window_seconds: int) -> None:
        self.max_failures = max_failures
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

    def check(self, *keys_and_limits: tuple[str, int]) -> None:
        now = time.monotonic()
        for key, limit in keys_and_limits:
            entries = self._recent(key, now)
            if len(entries) >= limit:
                retry_after = max(1, int(self.window_seconds - (now - entries[0])) + 1)
                raise RateLimitedError(
                    "Too many failed sign-in attempts; try again later",
                    details={"retry_after_seconds": retry_after},
                )

    def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        if len(self._failures) >= _MAX_KEYS:
            # Bound memory: drop the key whose newest failure is oldest.
            stalest = min(self._failures, key=lambda k: self._failures[k][-1])
            del self._failures[stalest]
        for key in keys:
            self._failures.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
