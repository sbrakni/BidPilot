"""Response cache keyed by `(prompt_hash, model)` (SPEC §17.4).

The saving is not theoretical. Re-opening an analysis, a retried job, and the eval harness
running the same corpus twice all send identical prompts; without a cache each costs a full
generation. §17.4 asks for it by name, under "dedup repeated analyses".

In-process by default, with a pluggable store so a deployment can put Redis behind it. That
default is honest rather than lazy: a worker handles one analysis at a time, so most repeats
happen inside one process, and an in-process dict cannot go stale against a shared store.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Protocol


class CacheStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any], ttl_seconds: float) -> None: ...


#: A day: long enough that a retried job or a re-opened analysis hits, short enough that a
#: corrected prompt is not served from yesterday for a week.
DEFAULT_TTL_SECONDS = 24 * 60 * 60

#: Entries, not bytes. An extraction result is a few hundred kilobytes at most, and bounding the
#: count keeps a long-running worker's footprint predictable.
DEFAULT_MAX_ENTRIES = 256


class MemoryStore:
    """LRU with expiry. Small, and the only store needed until a second worker shares one."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES, clock: Any = time.monotonic) -> None:
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._max_entries = max_entries
        self._clock = clock

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: dict[str, Any], ttl_seconds: float) -> None:
        self._entries[key] = (self._clock() + ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class ResponseCache:
    """What the gateway talks to. Thin, so swapping the store changes nothing above it."""

    def __init__(self, store: CacheStore | None = None, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._store = store or MemoryStore()
        self._ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._store.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._store.set(key, value, self._ttl)
