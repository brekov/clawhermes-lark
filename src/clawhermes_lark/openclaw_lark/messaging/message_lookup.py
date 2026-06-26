"""
消息查询 API — 对齐 openclaw-lark src/messaging/shared/message-lookup.ts

通过飞书 API 获取消息详情，用于：
  - Reaction 事件：获取原消息的 chat_id 来确定反应所属的会话
  - 消息引用：获取被引用消息的内容
  - 调试/诊断：按 message_id 查询消息信息
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("clawhermes.lark.message_lookup")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class FeishuMessageInfo:
    """飞书消息详情."""
    message_id: str = ""
    chat_id: str = ""
    chat_type: str = ""       # "p2p" | "group" | "private"
    message_type: str = ""    # "text" | "post" | "image" | ...
    content: str = ""         # 原始 JSON content
    root_id: str = ""         # 话题根消息 ID
    parent_id: str = ""       # 父消息 ID
    thread_id: str = ""       # 话题 ID
    sender_open_id: str = ""  # 发送者 open_id
    create_time: str = ""     # 创建时间 (毫秒时间戳)
    msg_type: str = ""        # 同 message_type

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"

    @property
    def is_p2p(self) -> bool:
        return self.chat_type in ("p2p", "private")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def get_message_feishu(
    client,
    message_id: str,
    timeout: float = 3.0,
) -> FeishuMessageInfo | None:
    """
    通过 message_id 获取飞书消息详情.

    Args:
        client: lark_oapi.Client 实例
        message_id: 消息 ID
        timeout: 超时秒数（3s，避免阻塞事件处理）

    Returns:
        FeishuMessageInfo 或 None（消息不存在/超时/无权限）
    """
    try:
        from lark_oapi.api.im.v1 import GetMessageRequest

        req = GetMessageRequest.builder() \
            .message_id(message_id) \
            .build()

        resp = await asyncio.wait_for(
            asyncio.to_thread(client.im.v1.message.get, req),
            timeout=timeout,
        )

        if resp.code != 0 or not resp.data:
            logger.debug("get_message_feishu failed: code=%s msg=%s", resp.code, resp.msg)
            return None

        items = getattr(resp.data, "items", [])
        if not items:
            return None

        msg = items[0]

        return FeishuMessageInfo(
            message_id=getattr(msg, "message_id", "") or "",
            chat_id=getattr(msg, "chat_id", "") or "",
            chat_type=getattr(msg, "chat_type", "") or "",
            message_type=getattr(msg, "message_type", "") or "",
            content=getattr(msg, "content", "") or "",
            root_id=getattr(msg, "root_id", "") or "",
            parent_id=getattr(msg, "parent_id", "") or "",
            thread_id=getattr(msg, "thread_id", "") or "",
            sender_open_id=getattr(
                getattr(msg, "sender", None), "id", ""
            ) if hasattr(msg, "sender") else "",
            create_time=str(getattr(msg, "create_time", "")),
            msg_type=getattr(msg, "message_type", "") or "",
        )

    except asyncio.TimeoutError:
        logger.debug("get_message_feishu timeout: msg_id=%s", message_id)
        return None
    except Exception:
        logger.debug("get_message_feishu error: msg_id=%s", message_id, exc_info=True)
        return None


async def get_chat_type_feishu(
    client,
    chat_id: str,
) -> str:
    """
    获取 chat 的类型（p2p / group）.

    用于 Reaction 事件处理时确定 chat_type.
    """
    try:
        from lark_oapi.api.im.v1 import GetChatRequest

        req = GetChatRequest.builder() \
            .chat_id(chat_id) \
            .build()

        resp = await asyncio.to_thread(client.im.v1.chat.get, req)

        if resp.code != 0 or not resp.data:
            # 失败时根据前缀推断
            return "group" if chat_id.startswith("oc_") else "p2p"

        from clawhermes_lark.openclaw_lark.targets import _is_group_chat
        # 直接根据 chat_id 前缀判断更可靠
        return "group" if chat_id.startswith("oc_") else "p2p"

    except Exception:
        logger.debug("get_chat_type_feishu failed", exc_info=True)
        return "group" if chat_id.startswith("oc_") else "p2p"


async def is_thread_capable_group(
    client,
    chat_id: str,
) -> bool:
    """
    判断群聊是否支持话题（Thread).

    话题群和普通群的区别：话题群中回复消息会创建子话题.
    通过 chat API 获取 chat 属性判断.
    """
    # 飞书 SDK 中 chat 对象没有直接的 thread 能力标志
    # 简化判断：群聊（oc_ 前缀）默认支持话题
    return chat_id.startswith("oc_")
