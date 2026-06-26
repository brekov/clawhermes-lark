"""
CardKit 实体管理 — 对齐 openclaw-lark src/card/cardkit.ts

管理飞书交互式卡片的完整生命周期：
  - create_card_entity: 创建卡片（发送 → 记录 message_id）
  - update_card: 更新已发送的卡片
  - stream_card_content: 流式更新卡片内容
  - 卡片 message_id 追踪和状态管理

抽象层位于 builder/streaming 和 SDK API 之间.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("clawhermes.lark.cardkit")

# ---------------------------------------------------------------------------
# Card Entity
# ---------------------------------------------------------------------------


class CardEntity:
    """
    卡片实体 — 追踪一张已发送的交互式卡片.

    对应飞书 API 返回的 message_id，用于后续更新操作.
    """

    __slots__ = ("card_id", "message_id", "chat_id", "sequence", "card_type")

    def __init__(
        self,
        card_id: str = "",
        message_id: str = "",
        chat_id: str = "",
        card_type: str = "interactive",
    ):
        self.card_id = card_id or message_id  # card_id 可等于 message_id
        self.message_id = message_id
        self.chat_id = chat_id
        self.sequence = 0  # 更新序列号
        self.card_type = card_type

    def increment_sequence(self) -> int:
        """递增更新序列号."""
        self.sequence += 1
        return self.sequence


# ---------------------------------------------------------------------------
# CardKit API
# ---------------------------------------------------------------------------


async def create_card_entity(
    adapter: Any,
    chat_id: str,
    card: dict[str, Any],
    reply_to_message_id: str = "",
    card_id: str = "",
) -> CardEntity | None:
    """
    创建卡片实体 — 发送卡片并记录 message_id.

    Args:
        adapter: LarkAdapter 实例
        chat_id: 目标 chat_id
        card: 卡片 payload
        reply_to_message_id: 回复目标消息 ID
        card_id: 自定义卡片 ID（可选）

    Returns:
        CardEntity 或 None（发送失败）
    """
    try:
        msg_id = await adapter._send_card_message(
            chat_id=chat_id,
            card=card,
            reply_msg_id=reply_to_message_id or None,
        )

        if not msg_id:
            logger.warning("create_card_entity: send failed")
            return None

        entity = CardEntity(
            card_id=card_id or msg_id,
            message_id=msg_id,
            chat_id=chat_id,
        )
        logger.debug("CardEntity created: id=%s msg_id=%s", entity.card_id, msg_id)
        return entity

    except Exception:
        logger.exception("create_card_entity failed")
        return None


async def send_card_by_card_id(
    adapter: Any,
    chat_id: str,
    card: dict[str, Any],
    card_id: str = "",
) -> CardEntity | None:
    """通过 card_id 发送卡片（create 的别名）."""
    return await create_card_entity(
        adapter=adapter,
        chat_id=chat_id,
        card=card,
        card_id=card_id,
    )


async def update_card(
    adapter: Any,
    entity: CardEntity,
    card: dict[str, Any],
) -> bool:
    """
    更新已发送的卡片.

    使用 PatchMessage API 更新卡片内容.
    """
    try:
        result = await adapter._update_card_message(
            message_id=entity.message_id,
            card=card,
        )
        if result:
            entity.increment_sequence()
            logger.debug("CardEntity updated: id=%s seq=%d", entity.card_id, entity.sequence)
        return bool(result)

    except Exception:
        logger.exception("update_card failed")
        return False


async def stream_card_content(
    adapter: Any,
    entity: CardEntity,
    content: str,
    element_id: str = "streaming_content",
) -> bool:
    """
    流式更新卡片中的指定元素内容.

    构建最小化卡片 payload，仅更新目标元素.
    """
    card = {
        "schema": "2.0",
        "body": {
            "elements": [{
                "tag": "markdown",
                "content": content,
                "element_id": element_id,
            }],
        },
    }
    return await update_card(adapter, entity, card)


async def set_card_streaming_mode(
    adapter: Any,
    entity: CardEntity,
    active: bool = True,
) -> bool:
    """
    设置卡片的流式模式标志.

    在卡片 config 中设置 update_multi 允许多次更新.
    """
    # update_multi 在创建卡片时设置，后续更新不需要修改
    # 此方法主要用于统一接口
    return True
