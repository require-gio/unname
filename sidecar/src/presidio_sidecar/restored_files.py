"""Restored files awaiting retrieval by the shell.

Self-contained module without service dependencies so that
``services.py`` can keep the store without creating an import cycle.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(slots=True)
class RestoredFile:
    """A restored file kept only in the sidecar's memory."""

    id: str
    name: str
    format: str
    content: bytes = field(repr=False)
    sha256: str
    entry_id: str | None
    tokens: frozenset[str] = field(repr=False)
    created: float
    recorded: bool = False


class RestoredFileStore:
    """A small cache of restored files until the shell retrieves them.

    Files contain real names and are never written to disk. Unretrieved
    entries expire after ``ttl_seconds``; the store also evicts oldest
    items when ``max_items`` or ``max_bytes`` limits are exceeded.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 30 * 60,
        max_items: int = 8,
        max_bytes: int = 256 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._items: OrderedDict[str, RestoredFile] = OrderedDict()
        self._ttl = ttl_seconds
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._clock = clock
        self._lock = threading.Lock()

    def put(
        self,
        *,
        name: str,
        format: str,
        content: bytes,
        entry_id: str | None,
        tokens: frozenset[str],
    ) -> RestoredFile:
        item = RestoredFile(
            id=secrets.token_hex(12),
            name=name,
            format=format,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            entry_id=entry_id,
            tokens=tokens,
            created=self._clock(),
        )
        with self._lock:
            self._expire()
            self._items[item.id] = item
            while len(self._items) > self._max_items or (
                len(self._items) > 1
                and sum(len(stored.content) for stored in self._items.values()) > self._max_bytes
            ):
                self._items.popitem(last=False)
        return item

    def get(self, file_id: str) -> RestoredFile | None:
        with self._lock:
            self._expire()
            return self._items.get(file_id)

    def mark_recorded(self, file_id: str) -> bool:
        """Returns True on first retrieval; subsequent calls return False."""

        with self._lock:
            item = self._items.get(file_id)
            if item is None or item.recorded:
                return False
            item.recorded = True
            return True

    def discard(self, file_id: str) -> bool:
        with self._lock:
            return self._items.pop(file_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _expire(self) -> None:
        now = self._clock()
        for file_id in [key for key, item in self._items.items() if now - item.created > self._ttl]:
            del self._items[file_id]


__all__ = ["RestoredFile", "RestoredFileStore"]
