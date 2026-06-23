"""
ClawHermes-Lark — 飞书/Lark 渠道适配器
基于 lark-oapi 官方 SDK，参考 larksuite/openclaw-lark 设计模式

作为独立 pip 包发布：pip install clawhermes-lark
"""
from clawhermes.channel.adapters.feishu import (  # type: ignore[import-not-found]
    FeishuAdapter,
    FeishuConfig,
    create_feishu_adapter,
)

__version__ = "0.1.0"
__all__ = ["FeishuAdapter", "FeishuConfig", "create_feishu_adapter"]
