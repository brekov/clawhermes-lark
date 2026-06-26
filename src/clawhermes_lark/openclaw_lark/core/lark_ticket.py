"""
请求追踪票据 — 对齐 openclaw-lark src/core/lark-ticket.ts

为每个入站消息生成 ticket，追踪处理信息：
  - message_id, chat_id, account_id
  - sender_open_id, chat_type, thread_id
  - start_time, elapsed time

用于调试、日志关联、性能监控.
"""
from __future__ import annotations

import logging
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("clawhermes.lark.lark_ticket")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class LarkTicket:
    """单个请求的追踪票据."""
    message_id: str
    chat_id: str
    account_id: str
    start_time: float = field(default_factory=time.time)
    sender_open_id: str = ""
    chat_type: str = ""       # "p2p" | "group"
    thread_id: str | None = None

    @property
    def elapsed_ms(self) -> int:
        """处理耗时（毫秒）."""
        return int((time.time() - self.start_time) * 1000)

    @property
    def elapsed_seconds(self) -> float:
        """处理耗时（秒）."""
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# Ticket store (process-level, thread-safe)
# ---------------------------------------------------------------------------

# 当前活跃的 ticket 列表（最近 N 个）
_active_tickets: "OrderedDict[str, LarkTicket]" = OrderedDict()
_max_tickets = 200
_lock = threading.Lock()

# 当前线程上下文中的 ticket（用于工具调用中获取上下文）
_ticket_context = threading.local()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_ticket(
    message_id: str = "",
    chat_id: str = "",
    account_id: str = "default",
    sender_open_id: str = "",
    chat_type: str = "",
    thread_id: str | None = None,
) -> LarkTicket:
    """创建请求追踪票据."""
    ticket = LarkTicket(
        message_id=message_id,
        chat_id=chat_id,
        account_id=account_id,
        sender_open_id=sender_open_id,
        chat_type=chat_type,
        thread_id=thread_id,
    )
    return ticket


def track_ticket(ticket: LarkTicket) -> None:
    """将 ticket 加入活跃追踪列表."""
    with _lock:
        key = f"{ticket.account_id}:{ticket.message_id}"
        _active_tickets[key] = ticket
        while len(_active_tickets) > _max_tickets:
            _active_tickets.popitem(last=False)


def untrack_ticket(ticket: LarkTicket) -> None:
    """从活跃追踪列表移除 ticket."""
    with _lock:
        key = f"{ticket.account_id}:{ticket.message_id}"
        _active_tickets.pop(key, None)


def get_ticket(message_id: str, account_id: str = "default") -> LarkTicket | None:
    """按 message_id 查找 ticket."""
    with _lock:
        return _active_tickets.get(f"{account_id}:{message_id}")


def get_active_tickets() -> list[LarkTicket]:
    """获取所有活跃 ticket."""
    with _lock:
        return list(_active_tickets.values())


def set_current_ticket(ticket: LarkTicket | None) -> None:
    """设置当前线程上下文的 ticket."""
    _ticket_context.ticket = ticket


def get_current_ticket() -> LarkTicket | None:
    """获取当前线程上下文的 ticket."""
    return getattr(_ticket_context, "ticket", None)


def ticket_elapsed(ticket: LarkTicket) -> str:
    """格式化为可读的耗时字符串."""
    ms = ticket.elapsed_ms
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


class with_ticket:
    """
    上下文管理器 — 在处理期间设置 ticket 上下文.

    用法:
        ticket = create_ticket(message_id=msg_id, chat_id=chat_id)
        track_ticket(ticket)
        with with_ticket(ticket):
            await process_message()
        untrack_ticket(ticket)
    """

    def __init__(self, ticket: LarkTicket):
        self._ticket = ticket
        self._prev: LarkTicket | None = None

    def __enter__(self):
        self._prev = get_current_ticket()
        set_current_ticket(self._ticket)
        return self

    def __exit__(self, *args):
        set_current_ticket(self._prev)

    async def __aenter__(self):
        self._prev = get_current_ticket()
        set_current_ticket(self._ticket)
        return self

    async def __aexit__(self, *args):
        set_current_ticket(self._prev)
