"""
Structured tool-use display for Feishu cards.

Aligns with larksuite/openclaw-lark src/card/tool-use-display.ts:
  - Tool descriptors with icons and names
  - Normalization of tool names
  - Parameter sanitization (redact secrets)
  - Building markdown content for tool use steps
  - Status-aware rendering (pending/running/done/error)

Produces markdown content suitable for embedding in card elements.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("clawhermes.lark.tool_use_display")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMPTY_TOOL_USE_PLACEHOLDER = "No tool steps available"

# Tool status icons
STATUS_ICONS = {
    "pending": "⏳",
    "running": "🔄",
    "done": "✅",
    "error": "❌",
    "aborted": "🛑",
}

# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------

ToolDescriptor = dict[str, Any]

TOOL_DESCRIPTORS: list[ToolDescriptor] = [
    {
        "aliases": ["skill"],
        "icon": "🔧",
        "title": "Load skill",
        "sanitizer": "skill",
        "param_keys": ["skill", "name"],
    },
    {
        "aliases": ["read", "open"],
        "icon": "📄",
        "title": "Read",
        "sanitizer": "path",
        "param_keys": ["file_path", "path", "file"],
    },
    {
        "aliases": ["write", "edit"],
        "icon": "✏️",
        "title": "Edit",
        "sanitizer": "path",
        "param_keys": ["file_path", "path", "file"],
    },
    {
        "aliases": ["web_search", "web-search", "search"],
        "icon": "🔍",
        "title": "Search web",
        "sanitizer": "search",
        "param_keys": ["query", "q"],
    },
    {
        "aliases": ["exec_command", "exec", "command", "bash", "shell"],
        "icon": "💻",
        "title": "Run command",
        "sanitizer": "command",
        "param_keys": ["cmd", "command"],
    },
    {
        "aliases": ["browser", "browse", "web_fetch", "web-fetch", "fetch"],
        "icon": "🌐",
        "title": "Browse",
        "sanitizer": "url",
        "param_keys": ["url", "link"],
    },
    {
        "aliases": ["grep", "rg", "search_content"],
        "icon": "🔎",
        "title": "Search code",
        "sanitizer": "search",
        "param_keys": ["pattern", "query"],
    },
    {
        "aliases": ["list_files", "ls", "dir"],
        "icon": "📂",
        "title": "List files",
        "sanitizer": "path",
        "param_keys": ["path", "dir"],
    },
    {
        "aliases": ["apply_patch", "patch", "edit_file"],
        "icon": "📝",
        "title": "Apply patch",
        "sanitizer": "path",
        "param_keys": ["path", "file"],
    },
    {
        "aliases": ["send_message", "send", "reply"],
        "icon": "💬",
        "title": "Send message",
        "sanitizer": "generic",
        "param_keys": ["to", "target", "chat_id"],
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_descriptor(tool_name: str) -> ToolDescriptor | None:
    """Find the best-matching tool descriptor for a given tool name."""
    normalized = tool_name.lower().replace("-", "_").strip()
    for desc in TOOL_DESCRIPTORS:
        for alias in desc["aliases"]:
            if normalized == alias or normalized.startswith(alias):
                return desc
    return None


def _sanitize_param(value: Any, kind: str) -> str:
    """Sanitize a tool parameter value for display."""
    if kind == "skill":
        return str(value).split("/")[-1].split("\\")[-1] if value else ""
    if kind == "path":
        s = str(value)
        return s.split("/")[-1] if "/" in s else s
    if kind in ("search", "command", "url"):
        s = str(value)
        if len(s) > 80:
            return s[:80] + "…"
        return s
    return str(value)[:100] if value else ""


def _redact_secrets(text: str) -> str:
    """Redact potential secrets/keys from display text."""
    # Redact API keys, tokens, passwords
    text = re.sub(r'(api[_-]?key|apikey|secret|token|password|passwd)\s*[=:]\s*\S+',
                  r'\1=***', text, flags=re.IGNORECASE)
    text = re.sub(r'(Bearer\s+)\S+', r'\1***', text)
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_tool_use_display(
    tool_name: str,
    params: dict[str, Any] | str | None = None,
    result: Any = None,
    error: str | None = None,
    status: str = "done",
    duration_ms: int | None = None,
) -> str:
    """
    Build a markdown-formatted display string for a single tool use step.

    Returns a markdown string suitable for embedding in card elements.
    """
    desc = _find_descriptor(tool_name)
    icon = desc["icon"] if desc else "🔧"
    title = desc["title"] if desc else tool_name

    # Build param summary
    param_str = ""
    if params:
        if isinstance(params, str):
            param_str = f"`{_sanitize_param(params, 'generic')}`"
        elif isinstance(params, dict) and desc:
            for key in desc.get("param_keys", []):
                if key in params:
                    val = params[key]
                    param_str = f"`{_sanitize_param(val, desc['sanitizer'])}`"
                    break
            if not param_str and params:
                # Take the first meaningful param
                for k, v in params.items():
                    if k not in ("_meta", "context"):
                        param_str = f"`{_sanitize_param(v, desc['sanitizer'])}`"
                        break

    status_icon = STATUS_ICONS.get(status, "🔄")
    line = f"{status_icon} {icon} **{title}**"

    if param_str:
        line += f" {param_str}"

    if duration_ms is not None:
        seconds = duration_ms / 1000
        if seconds >= 1:
            line += f" ({seconds:.1f}s)"
        else:
            line += f" ({duration_ms}ms)"

    if error:
        line += f"\n  ❌ Error: `{error[:200]}`"

    return line


def build_tool_use_summary(
    steps: list[dict[str, Any]],
    max_steps: int = 10,
) -> str:
    """
    Build a summary of multiple tool use steps.

    Args:
        steps: List of step dicts with keys:
            tool_name, params, result, error, status, duration_ms
        max_steps: Maximum number of steps to display

    Returns:
        Markdown string for card embedding.
    """
    if not steps:
        return EMPTY_TOOL_USE_PLACEHOLDER

    lines: list[str] = []
    for i, step in enumerate(steps[:max_steps]):
        display = build_tool_use_display(
            tool_name=step.get("tool_name", "unknown"),
            params=step.get("params"),
            result=step.get("result"),
            error=step.get("error"),
            status=step.get("status", "done"),
            duration_ms=step.get("duration_ms"),
        )
        lines.append(display)

    if len(steps) > max_steps:
        remaining = len(steps) - max_steps
        lines.append(f"*… 还有 {remaining} 个工具调用*")

    return "\n".join(lines)


def build_tool_use_title_suffix(
    tool_name: str | None = None,
    step_count: int = 0,
) -> str:
    """Build a title suffix for the card header showing tool use activity."""
    if tool_name and step_count > 0:
        return f" — 调用工具中 ({step_count})"
    elif step_count > 0:
        return f" — 已使用 {step_count} 个工具"
    return ""


def normalize_tool_name(name: str) -> str:
    """Normalize a tool name to a consistent format."""
    return name.lower().replace("-", "_").strip()
