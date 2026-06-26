"""
Chat task queue — process-level singleton for serial per-chat message handling.

Aligns with larksuite/openclaw-lark src/channel/chat-queue.ts:
  - Serial task execution per (account_id, chat_id, thread_id)
  - Immediate vs queued status reporting
  - Thread-scoped keys for topic group support
  - Active dispatcher registration for abort fast-path

Used by adapter (WebSocket inbound), interactive dispatch, and synthetic
message paths to ensure message ordering without per-chat races.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Callable

logger = logging.getLogger("clawhermes.lark.chat_queue")

# ---------------------------------------------------------------------------
# Queue state
# ---------------------------------------------------------------------------

# Serial queue for each chat key — maps key → tail promise
_chat_queues: dict[str, asyncio.Task | asyncio.Future] = {}

# Active dispatcher registry — maps key → dispatcher entry for abort fast-path
_active_dispatchers: dict[str, "ActiveDispatcherEntry"] = {}

# Max entries in each map before LRU eviction
_MAX_QUEUE_ENTRIES = 10_000
_MAX_DISPATCHER_ENTRIES = 5_000


class ActiveDispatcherEntry:
    """Entry registered for a chat key enabling abort-card fast-path."""
    __slots__ = ("abort_card", "abort_controller")
    abort_card: Callable[[], asyncio.Future] | None
    abort_controller: asyncio.Task | None

    def __init__(
        self,
        abort_card: Callable[[], asyncio.Future] | None = None,
        abort_controller: asyncio.Task | None = None,
    ):
        self.abort_card = abort_card
        self.abort_controller = abort_controller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def thread_scoped_key(base: str, thread_id: str | None = None) -> str:
    """Append :thread:{threadId} suffix when threadId is present."""
    return f"{base}:thread:{thread_id}" if thread_id else base


def build_queue_key(
    account_id: str, chat_id: str, thread_id: str | None = None
) -> str:
    return thread_scoped_key(f"{account_id}:{chat_id}", thread_id)


def _lru_evict_map(d: dict, max_entries: int) -> None:
    """Evict oldest entries from an OrderedDict or standard dict."""
    if isinstance(d, OrderedDict):
        while len(d) > max_entries:
            d.popitem(last=False)
    else:
        while len(d) > max_entries:
            d.pop(next(iter(d)), None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_active_dispatcher(key: str, entry: ActiveDispatcherEntry) -> None:
    """Register an active dispatcher for a chat key (abort fast-path)."""
    _active_dispatchers[key] = entry
    _lru_evict_map(_active_dispatchers, _MAX_DISPATCHER_ENTRIES)


def unregister_active_dispatcher(key: str) -> None:
    """Remove an active dispatcher entry."""
    _active_dispatchers.pop(key, None)


def get_active_dispatcher(key: str) -> ActiveDispatcherEntry | None:
    """Get the active dispatcher for a chat key, if any."""
    return _active_dispatchers.get(key)


def has_active_task(key: str) -> bool:
    """Check whether the queue has an active task for the given key."""
    return key in _chat_queues


def enqueue_feishu_chat_task(
    account_id: str,
    chat_id: str,
    thread_id: str | None,
    task: Callable[[], asyncio.Future],
) -> tuple[str, asyncio.Task]:
    """
    Enqueue a task for serial execution on a chat key.

    Returns (status, task_promise) where status is "immediate" or "queued".
    """
    key = build_queue_key(account_id, chat_id, thread_id)

    prev_future = _chat_queues.get(key)
    status = "queued" if prev_future and not prev_future.done() else "immediate"

    async def _runner():
        """Execute the task after the previous one completes."""
        if prev_future and not prev_future.done():
            try:
                await prev_future
            except Exception:
                pass
        await task()

    # Cleanup: remove from queue when done
    async def _with_cleanup():
        try:
            await _runner()
        finally:
            if _chat_queues.get(key) is task_obj:
                _chat_queues.pop(key, None)

    task_obj = asyncio.ensure_future(_with_cleanup())
    _chat_queues[key] = task_obj
    _lru_evict_map(_chat_queues, _MAX_QUEUE_ENTRIES)

    return status, task_obj


def get_queue_size() -> int:
    """Return the number of active chat queue entries (for diagnostics)."""
    return len(_chat_queues)


def reset_chat_queue_state() -> None:
    """Reset all queue and dispatcher state (test-only)."""
    _chat_queues.clear()
    _active_dispatchers.clear()
