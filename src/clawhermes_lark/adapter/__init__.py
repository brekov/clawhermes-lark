"""
ClawHermes-Lark 适配层 — ChannelAdapter 实现 + LarkClient

本层负责对接 ClawHermes Agent 框架，实现 ChannelAdapter 接口：
  - LarkAdapter: 飞书消息渠道适配器
  - LarkClient:  飞书 SDK 客户端管理器
  - LarkConfig:  渠道配置
  - LarkEventType: 事件类型枚举
"""
from clawhermes_lark.adapter.adapter import (
    LarkAdapter,
    LarkConfig,
    LarkEventType,
    create_lark_adapter,
)
from clawhermes_lark.adapter.client import BotIdentity, LarkClient

__all__ = [
    "LarkAdapter",
    "LarkConfig",
    "LarkEventType",
    "create_lark_adapter",
    "BotIdentity",
    "LarkClient",
]
