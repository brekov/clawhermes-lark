"""
共享工具辅助函数 — 对齐 openclaw-lark src/tools/helpers.ts

提供 OAPI 工具和 onboarding 等模块的通用能力：
  - format_tool_result / format_tool_error: 统一的结果格式化
  - validate_required_params: 参数校验
  - validate_enum: 枚举值校验
  - create_tool_logger: 工具日志
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("clawhermes.lark.tools.helpers")

# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


def format_tool_result(data: Any) -> str:
    """将工具执行结果格式化为 JSON 字符串."""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(data)


def format_tool_error(error: str | Exception) -> str:
    """将错误格式化为统一的错误字符串."""
    msg = str(error)
    # 截断过长的错误信息
    if len(msg) > 500:
        msg = msg[:500] + "…"
    return f"Error: {msg}"


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def validate_required_params(
    params: dict[str, Any],
    required: list[str],
    tool_name: str = "unknown",
) -> str | None:
    """
    校验必填参数是否存在.

    Returns:
        None 表示通过，否则返回错误信息字符串.
    """
    missing = [k for k in required if k not in params or params[k] is None]
    if missing:
        return f"Tool '{tool_name}' missing required params: {', '.join(missing)}"
    return None


def validate_enum(
    value: str,
    allowed: list[str],
    param_name: str,
    tool_name: str = "unknown",
) -> str | None:
    """
    校验参数值是否在允许的枚举值集合中.

    Returns:
        None 表示通过，否则返回错误信息字符串.
    """
    if value not in allowed:
        return (
            f"Tool '{tool_name}': invalid value '{value}' for param '{param_name}'. "
            f"Allowed: {', '.join(allowed)}"
        )
    return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def create_tool_logger(tool_name: str) -> logging.Logger:
    """为工具创建带命名空间的 logger."""
    return logging.getLogger(f"clawhermes.lark.tools.{tool_name}")


# ---------------------------------------------------------------------------
# Safe JSON
# ---------------------------------------------------------------------------


def safe_json_parse(raw: str, default: Any = None) -> Any:
    """安全解析 JSON，失败返回 default."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def safe_json_dumps(obj: Any, default: str = "{}") -> str:
    """安全序列化 JSON，失败返回 default."""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return default
