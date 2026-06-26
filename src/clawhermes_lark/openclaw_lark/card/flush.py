"""
Flush controller — throttle card update sends to avoid Feishu rate limits.

Aligns with larksuite/openclaw-lark src/card/flush-controller.ts.
Ensures card updates are sent at a controlled interval without
overwhelming the Feishu API.

Lock is created lazily to avoid requiring a running event loop at
construction time.
"""
from __future__ import annotations

import asyncio
import time
import logging

logger = logging.getLogger("clawhermes.lark.flush_controller")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FLUSH_INTERVAL_MS = 500   # Minimum interval between card updates
DEFAULT_MAX_BATCH_SIZE = 10       # Max pending updates before forced flush


class FlushController:
    """
    Controls the rate at which card content updates are flushed to Feishu.

    Batches rapid updates and sends them at a controlled interval to avoid
    triggering rate limits on the Feishu card update API.
    """

    __slots__ = (
        "_interval_ms",
        "_max_batch",
        "_pending",
        "_last_flush",
        "_flush_task",
        "_lock",  # created lazily
    )

    def __init__(
        self,
        interval_ms: int = DEFAULT_FLUSH_INTERVAL_MS,
        max_batch: int = DEFAULT_MAX_BATCH_SIZE,
    ):
        self._interval_ms = interval_ms
        self._max_batch = max_batch
        self._pending: list[dict] = []
        self._last_flush = 0.0
        self._flush_task: asyncio.Task | None = None
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Get or lazily create the async lock."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def should_flush(self, force: bool = False) -> bool:
        """Check if enough time has passed since the last flush."""
        if force:
            return True
        if len(self._pending) >= self._max_batch:
            return True
        now = time.time() * 1000
        return (now - self._last_flush) >= self._interval_ms

    async def enqueue(self, payload: dict) -> None:
        """Enqueue a card update payload for the next flush cycle."""
        async with self._get_lock():
            self._pending.append(payload)

    async def flush(self, sender: callable) -> bool:
        """
        Send all pending card updates via the provided sender function.

        Returns True if any updates were sent.
        """
        async with self._get_lock():
            if not self._pending:
                return False
            # Take the last payload (most recent state is what matters)
            payload = self._pending[-1]
            self._pending.clear()
            self._last_flush = time.time() * 1000

        try:
            await sender(payload)
            return True
        except Exception:
            logger.debug("FlushController: send failed", exc_info=True)
            return False

    async def flush_all(self, sender: callable) -> None:
        """Force-flush all pending updates before shutdown."""
        async with self._get_lock():
            payloads = list(self._pending)
            self._pending.clear()

        for payload in payloads:
            try:
                await sender(payload)
            except Exception:
                logger.debug("FlushController: force-flush failed", exc_info=True)

    def clear(self) -> None:
        """Discard all pending updates."""
        # Lock acquisition in synchronous context is not possible,
        # so we schedule the clear on the event loop.
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._pending.clear)
        except RuntimeError:
            # No running loop — clear directly (safe for sync context)
            self._pending.clear()

    @property
    def pending_count(self) -> int:
        """Number of pending updates."""
        return len(self._pending)
