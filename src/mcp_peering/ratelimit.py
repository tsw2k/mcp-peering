"""Minimum-interval async rate limiter for outbound API calls."""

from __future__ import annotations

import asyncio
from time import monotonic


class AsyncRateLimiter:
    """Spaces consecutive requests at least ``1 / rate`` seconds apart.

    ``rate`` is requests per second; 0 disables limiting. Concurrent callers
    are serialised on a lock and each receives its own slot, so bursts are
    delayed instead of dropped.
    """

    def __init__(self, rate: float = 0.0) -> None:
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    async def acquire(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            now = monotonic()
            if self._next_slot < now:
                self._next_slot = now
            delay = self._next_slot - now
            self._next_slot += self._interval
        if delay > 0:
            await asyncio.sleep(delay)
