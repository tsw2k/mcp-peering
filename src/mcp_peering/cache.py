"""Small async-safe TTL cache used to memoise PeeringDB GET responses."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Hashable
from time import monotonic
from typing import Any


class TTLCache:
    """LRU cache whose entries expire ``ttl`` seconds after being stored.

    ``maxsize`` or ``ttl`` of 0 disables caching entirely. Cached values are
    shared between callers and must be treated as read-only.
    """

    def __init__(self, maxsize: int = 0, ttl: float = 0.0) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._maxsize > 0 and self._ttl > 0

    async def get(self, key: Hashable) -> tuple[Any, bool]:
        """Return ``(value, True)`` for a live entry, ``(None, False)`` otherwise."""
        if not self.enabled:
            return None, False
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            expires_at, value = entry
            if monotonic() >= expires_at:
                del self._store[key]
                return None, False
            self._store.move_to_end(key)
            return value, True

    async def set(self, key: Hashable, value: Any) -> None:
        if not self.enabled:
            return
        async with self._lock:
            self._store[key] = (monotonic() + self._ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)
