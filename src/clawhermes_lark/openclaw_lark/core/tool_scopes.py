"""
工具权限 Scope 定义 — 对齐 openclaw-lark src/core/tool-scopes.ts

定义每个 OAPI 工具所需的飞书 API scope.
用于自动授权和设备授权流程中确定需要请求的权限.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 工具 → 所需 scope 映射
# ---------------------------------------------------------------------------

TOOL_SCOPES: dict[str, list[str]] = {
    # Sheets
    "sheets_get_meta": ["sheet:readonly"],
    "sheets_read_values": ["sheet:readonly"],
    "sheets_write_values": ["sheet:write"],
    "sheets_list": ["drive:drive:readonly"],
    # Calendar
    "calendar_list": ["calendar:calendar:readonly"],
    "calendar_list_events": ["calendar:calendar:readonly", "calendar:event:readonly"],
    "calendar_create_event": ["calendar:calendar:write", "calendar:event:write"],
    # Drive
    "drive_list_files": ["drive:drive:readonly"],
    "drive_download_file": ["drive:drive:readonly"],
    "drive_search": ["drive:drive:readonly"],
    # Wiki
    "wiki_list_spaces": ["wiki:wiki:readonly"],
    "wiki_get_node": ["wiki:wiki:readonly"],
    "wiki_list_nodes": ["wiki:wiki:readonly"],
    # Docs
    "docs_get_content": ["docx:document:readonly"],
    "docs_get_meta": ["docx:document:readonly"],
    # IM / Chat
    "im_get_chat_info": ["im:chat"],
    "im_list_chat_members": ["im:chat"],
    # Common
    "common_get_user": ["contact:user.base:readonly"],
    "common_search_users": ["contact:user.base:readonly"],
    # Search
    "search_enterprise": ["search:search:readonly"],
}


def get_tool_scopes(tool_name: str) -> list[str]:
    """获取指定工具所需的 scope 列表."""
    return TOOL_SCOPES.get(tool_name, [])


def get_all_scopes() -> list[str]:
    """获取所有工具需要的去重 scope 列表."""
    all_scopes: set[str] = set()
    for scopes in TOOL_SCOPES.values():
        all_scopes.update(scopes)
    return sorted(all_scopes)


def get_tools_by_scope(scope: str) -> list[str]:
    """获取需要指定 scope 的所有工具名称."""
    return [name for name, scopes in TOOL_SCOPES.items() if scope in scopes]
