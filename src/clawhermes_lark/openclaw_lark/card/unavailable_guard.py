"""
消息不可用检测 — 对齐 openclaw-lark src/card/unavailable-guard.ts

检测飞书 API 返回的"消息不可用"错误（例如消息已被撤回/删除），
并自动降级：将卡片消息转为普通文本发送，或跳过该消息.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("clawhermes.lark.unavailable_guard")

# ---------------------------------------------------------------------------
# 消息不可用错误码
# ---------------------------------------------------------------------------

# 常见飞书 API 错误码 — 消息不可用
UNAVAILABLE_ERROR_CODES: set[int] = {
    220001,  # message not found
    220002,  # message has been deleted/recalled
    220003,  # message not visible to the bot
    230001,  # message content invalid
    230002,  # message has been forwarded
}

# 错误信息关键词
UNAVAILABLE_KEYWORDS: list[str] = [
    "message not found",
    "message has been",
    "message is not",
    "no permission to",
    "已撤回",
    "已删除",
    "已被",
    "not visible",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_message_unavailable(code: int = 0, msg: str = "") -> bool:
    """
    判断是否因为消息不可用导致调用失败.

    飞书 API 在以下情况会返回消息不可用：
      - 消息已被撤回
      - 消息已被删除
      - Bot 无权访问该消息
      - 消息内容无效
    """
    if code in UNAVAILABLE_ERROR_CODES:
        return True

    if msg:
        msg_lower = msg.lower()
        return any(kw.lower() in msg_lower for kw in UNAVAILABLE_KEYWORDS)

    return False


def is_exception_unavailable(error: Exception) -> bool:
    """判断异常是否为消息不可用导致."""
    msg = str(error)

    # 检查错误码
    code = getattr(error, "code", 0)
    if code and is_message_unavailable(code=code):
        return True

    # 检查错误信息
    if msg and is_message_unavailable(msg=msg):
        return True

    return False


class UnavailableGuard:
    """
    消息不可用防护 — 自动降级处理.

    用于流式卡片和消息发送时检测"消息不可用"错误:
      - 卡片回复失败 → 降级为纯文本
      - 引用消息被删除 → 忽略引用
      - 回复目标被撤回 → 发送新消息而非回复
    """

    __slots__ = ("_hit_count", "_max_retries")

    def __init__(self, max_retries: int = 1):
        self._hit_count = 0
        self._max_retries = max_retries

    def check(self, error: Exception) -> bool:
        """检查错误是否为消息不可用，并记录计数."""
        if is_exception_unavailable(error):
            self._hit_count += 1
            logger.debug(
                "UnavailableGuard hit #%d (max=%d): %s",
                self._hit_count, self._max_retries, error,
            )
            return True
        return False

    @property
    def should_degrade(self) -> bool:
        """是否应该降级（遇到消息不可用错误）."""
        return self._hit_count > 0

    @property
    def should_abort(self) -> bool:
        """是否应该终止（超过最大重试次数）."""
        return self._hit_count > self._max_retries

    def reset(self) -> None:
        """重置计数器."""
        self._hit_count = 0

    async def run_with_guard(self, operation: callable, fallback: callable | None = None):
        """
        执行操作，遇到消息不可用时执行降级.

        Args:
            operation: 主操作（异步可调用）
            fallback: 降级操作（异步可调用），返回降级结果
        """
        try:
            return await operation()
        except Exception as e:
            if self.check(e) and fallback:
                logger.info("UnavailableGuard: degrading to fallback")
                return await fallback()
            raise
