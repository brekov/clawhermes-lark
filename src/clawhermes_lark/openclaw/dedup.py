"""
FIFO-based message deduplication with TTL and periodic sweep.

Aligns with larksuite/openclaw-lark src/messaging/inbound/dedup.ts:
  - FIFO eviction (not LRU) since message IDs are write-once/check-once
  - TTL-based expiry with periodic sweep
  - Optional scope prefix (e.g. accountId) for multi-account namespacing
  - Message expiry check for stale reconnect replays

Feishu WebSocket connections may redeliver messages on reconnect.
This module tracks recently-seen message/event IDs and filters duplicates.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("clawhermes.lark.dedup")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TTL_MS = 12 * 60 * 60 * 1000  # 12 hours
DEFAULT_MAX_ENTRIES = 5_000
DEFAULT_SWEEP_INTERVAL_S = 5 * 60  # 5 minutes
DEFAULT_EXPIRY_MS = 30 * 60 * 1000  # 30 minutes (message age expiry)

# ---------------------------------------------------------------------------
# Message expiry
# ---------------------------------------------------------------------------


def is_message_expired(
    create_time_str: str | None,
    expiry_ms: int = DEFAULT_EXPIRY_MS,
) -> bool:
    """
    Check whether a message is too old to process.

    Feishu message create_time is a millisecond Unix timestamp encoded
    as a string. When a WebSocket reconnects after a long outage, stale
    messages may be redelivered — this check lets callers discard them
    before entering the full handling pipeline.
    """
    if not create_time_str:
        return False
    try:
        create_time = int(create_time_str)
    except (TypeError, ValueError):
        return False
    import time
    now_ms = int(time.time() * 1000)
    return now_ms - create_time > expiry_ms


# ---------------------------------------------------------------------------
# MessageDedup
# ---------------------------------------------------------------------------


class MessageDedup:
    """
    FIFO-based message deduplication store with TTL and periodic sweep.

    Design:
      - OrderedDict preserves insertion order → natural FIFO eviction
      - Periodic sweep leverages FIFO ordering: iterate from oldest,
        break at first non-expired entry → O(expired), not O(n)
      - Sweep task starts lazily on first try_record call to avoid
        requiring a running event loop at construction time.
    """

    __slots__ = ("_store", "_ttl_ms", "_max_entries", "_sweep_task",
                 "_sweep_interval_s", "_sweep_started", "_running")

    def __init__(
        self,
        ttl_ms: int = DEFAULT_TTL_MS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        sweep_interval_s: int = DEFAULT_SWEEP_INTERVAL_S,
    ):
        self._store: "OrderedDict[str, int]" = OrderedDict()
        self._ttl_ms = ttl_ms
        self._max_entries = max_entries
        self._sweep_interval_s = sweep_interval_s
        self._sweep_task: asyncio.Task | None = None
        self._sweep_started = False
        self._running = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def try_record(self, msg_id: str, scope: str | None = None) -> bool:
        """
        Try to record a message/event ID.

        Returns:
          True if the ID is new (not a duplicate); False if it's a duplicate.
        """
        import time

        # Lazy-start the sweep task (requires a running event loop)
        self._ensure_sweep_started()

        key = f"{scope}:{msg_id}" if scope else msg_id
        now_ms = int(time.time() * 1000)

        existing = self._store.get(key)
        if existing is not None:
            # Entry exists — check TTL
            if now_ms - existing < self._ttl_ms:
                return False  # duplicate — still within TTL
            # Expired — remove so we can re-insert at tail (refresh position)
            del self._store[key]

        # Enforce capacity via FIFO: drop the oldest entry
        if len(self._store) >= self._max_entries:
            self._store.popitem(last=False)

        self._store[key] = now_ms
        return True

    @property
    def size(self) -> int:
        """Current number of tracked entries (for diagnostics)."""
        return len(self._store)

    def clear(self) -> None:
        """Remove all entries and stop the periodic sweep."""
        # Cancel sweep first to avoid a race where sweep fires
        # after _running is False but before task cancellation
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()
            self._sweep_task = None
        self._running = False
        self._store.clear()

    def dispose(self) -> None:
        """Alias for clear()."""
        self.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_sweep_started(self) -> None:
        """Start the sweep task lazily, after the event loop is available."""
        if self._sweep_started:
            return
        self._sweep_started = True
        if self._sweep_interval_s > 0:
            self._sweep_task = asyncio.ensure_future(
                self._sweep_loop(self._sweep_interval_s)
            )

    async def _sweep_loop(self, interval_s: int) -> None:
        """Periodic sweep of expired entries from the front of the map."""
        while self._running:
            try:
                await asyncio.sleep(interval_s)
                self._sweep()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("MessageDedup sweep error", exc_info=True)

    def _sweep(self) -> None:
        """Sweep expired entries by removing from front until non-expired."""
        import time
        now_ms = int(time.time() * 1000)

        to_remove: list[str] = []
        for key, ts in self._store.items():
            if now_ms - ts >= self._ttl_ms:
                to_remove.append(key)
            else:
                break  # FIFO: first non-expired means rest are also fresh

        for key in to_remove:
            self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_message_dedup(
    ttl_ms: int = DEFAULT_TTL_MS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> MessageDedup:
    """Create a MessageDedup instance with sensible defaults."""
    return MessageDedup(ttl_ms=ttl_ms, max_entries=max_entries)
