"""
ClawHermes-Lark — 飞书/Lark 渠道适配器
基于 lark-oapi 官方 SDK，完整实现 larksuite/openclaw-lark 核心功能

独立 pip 包：pip install clawhermes-lark
"""
from clawhermes_lark.adapter import (
    LarkAdapter,
    LarkConfig,
    LarkEventType,
    create_lark_adapter,
)
from clawhermes_lark.client import BotIdentity, LarkClient

__version__ = "0.1.0"
__all__ = ["LarkAdapter", "LarkConfig", "LarkEventType", "create_lark_adapter", "BotIdentity", "LarkClient"]
