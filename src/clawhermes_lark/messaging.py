"""
ClawHermes-Lark — 消息交互增强模块

对齐 larksuite/openclaw-lark messaging/:
  - Reactions (添加/移除/列出表情回应)
  - Typing indicators (通过 reactions 模拟"正在输入")
  - Message editing (编辑已发送消息)
  - Card messages (交互式卡片消息)

基于 lark-oapi 官方 SDK.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("clawhermes.lark.messaging")

# ============================================================================
# Reactions
# ============================================================================


@dataclass
class FeishuReaction:
    """飞书表情回应 — 对齐 openclaw-lark FeishuReaction"""
    reaction_id: str
    emoji_type: str
    operator_type: str  # "app" | "user"
    operator_id: str


async def add_reaction(
    client,
    message_id: str,
    emoji_type: str = "THUMBSUP",
) -> FeishuReaction | None:
    """给消息添加表情回应

    Args:
        client: lark_oapi.Client 实例
        message_id: 消息 ID (open_message_id)
        emoji_type: 表情类型 (THUMBSUP, HEART, LAUGH, etc.)

    Returns:
        FeishuReaction 若成功, None 若失败.
    """
    from lark_oapi.api.im.v1 import (
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
    )

    try:
        body = CreateMessageReactionRequestBody.builder() \
            .reaction_type(emoji_type) \
            .build()
        request = CreateMessageReactionRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()
        response = client.im.v1.message_reaction.create(request)
        if not response.success():
            logger.warning(f"add_reaction failed: {response.msg}")
            return None
        data = response.data
        if data is None:
            return None
        return FeishuReaction(
            reaction_id=getattr(data, "reaction_id", "") or "",
            emoji_type=emoji_type,
            operator_type="app",
            operator_id="",
        )
    except Exception as e:
        logger.warning(f"add_reaction error: {e}")
        return None


async def remove_reaction(
    client,
    message_id: str,
    reaction_id: str,
) -> bool:
    """移除消息上的表情回应

    Args:
        client: lark_oapi.Client 实例
        message_id: 消息 ID
        reaction_id: 回应 ID (从 add_reaction 获取)

    Returns:
        True 若成功.
    """
    from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

    try:
        request = DeleteMessageReactionRequest.builder() \
            .message_id(message_id) \
            .reaction_id(reaction_id) \
            .build()
        response = client.im.v1.message_reaction.delete(request)
        return response.success()
    except Exception as e:
        logger.warning(f"remove_reaction error: {e}")
        return False


async def list_reactions(
    client,
    message_id: str,
) -> list[FeishuReaction]:
    """列出消息上所有表情回应

    Args:
        client: lark_oapi.Client 实例
        message_id: 消息 ID

    Returns:
        回应列表.
    """
    from lark_oapi.api.im.v1 import ListMessageReactionRequest

    try:
        request = ListMessageReactionRequest.builder() \
            .message_id(message_id) \
            .build()
        response = client.im.v1.message_reaction.list(request)
        if not response.success() or not response.data:
            return []
        items = getattr(response.data, "items", []) or []
        result: list[FeishuReaction] = []
        for item in items:
            result.append(FeishuReaction(
                reaction_id=getattr(item, "reaction_id", "") or "",
                emoji_type=getattr(item, "reaction_type", "") or "",
                operator_type=getattr(item, "operator_type", "") or "",
                operator_id=getattr(item, "operator_id", "") or "",
            ))
        return result
    except Exception as e:
        logger.warning(f"list_reactions error: {e}")
        return []


# ============================================================================
# Typing Indicator
# ============================================================================


class TypingIndicator:
    """飞书"正在输入"状态模拟 — 对齐 openclaw-lark typing.ts

    飞书没有原生的 typing indicator API, 通过给用户消息添加
    表情回应来模拟 "bot 已收到消息, 正在处理" 的视觉反馈.

    使用方式:
        async with TypingIndicator(client, message_id):
            # 在此块内执行处理的逻辑
            ...
        # 退出块时自动移除 typing 回应
    """

    # 用于表示"正在输入"的表情类型
    TYPING_EMOJI = "CLOCK"  # 🕐 时钟表情

    def __init__(self, client, message_id: str):
        self._client = client
        self._message_id = message_id
        self._reaction_id: str | None = None
        self._active = False

    async def start(self) -> None:
        """开始 typing 状态 — 添加时钟回应"""
        if self._active:
            return
        reaction = await add_reaction(
            self._client, self._message_id, self.TYPING_EMOJI
        )
        if reaction:
            self._reaction_id = reaction.reaction_id
        self._active = True

    async def stop(self) -> None:
        """停止 typing 状态 — 移除时钟回应"""
        if not self._active:
            return
        if self._reaction_id:
            await remove_reaction(
                self._client, self._message_id, self._reaction_id
            )
        self._active = False
        self._reaction_id = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()


# ============================================================================
# Message Editing
# ============================================================================


async def edit_message(
    client,
    message_id: str,
    content: str,
    msg_type: str = "text",
) -> bool:
    """编辑已发送的消息

    对齐 openclaw-lark editMessageFeishu.
    仅支持纯文本编辑 (飞书 API 限制).

    Args:
        client: lark_oapi.Client 实例
        message_id: 消息 ID
        content: 新内容 (JSON 字符串)
        msg_type: 消息类型

    Returns:
        True 若成功.
    """
    from lark_oapi.api.im.v1 import (
        PatchMessageRequest,
        PatchMessageRequestBody,
    )

    try:
        body = PatchMessageRequestBody.builder() \
            .content(content) \
            .build()
        request = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()
        response = client.im.v1.message.patch(request)
        return response.success()
    except Exception as e:
        logger.warning(f"edit_message error: {e}")
        return False


# ============================================================================
# Card Messages
# ============================================================================


def build_markdown_card(text: str) -> dict[str, Any]:
    """构建 Markdown 卡片消息 payload

    对齐 openclaw-lark buildMarkdownCard.

    Args:
        text: Markdown 文本内容

    Returns:
        飞书消息卡片 JSON payload.
    """
    import json

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "wathet",
            "title": {"content": "ClawHermes", "tag": "plain_text"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": text,
            }
        ],
    }
    return {
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }


async def send_card(
    client,
    receive_id: str,
    card: dict[str, Any],
    receive_id_type: str = "chat_id",
) -> bool:
    """发送交互式卡片消息

    Args:
        client: lark_oapi.Client 实例
        receive_id: 接收者 ID
        card: 卡片 payload (来自 build_markdown_card 或自定义)
        receive_id_type: 接收者类型

    Returns:
        True 若成功.
    """
    import json
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )

    try:
        content = json.dumps(card, ensure_ascii=False)
        body = CreateMessageRequestBody.builder() \
            .receive_id(receive_id) \
            .msg_type("interactive") \
            .content(content) \
            .build()
        request = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(body) \
            .build()
        response = client.im.v1.message.create(request)
        return response.success()
    except Exception as e:
        logger.warning(f"send_card error: {e}")
        return False
