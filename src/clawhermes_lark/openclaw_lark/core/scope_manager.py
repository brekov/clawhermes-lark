"""
Scope 权限管理 — 对齐 openclaw-lark src/core/scope-manager.ts

管理飞书 API 权限 scope：
  - 检查应用已授权的 scope 列表
  - 判断是否满足工具调用所需的 scope
  - 过滤敏感 scope（不在卡牌中展示）
"""
from __future__ import annotations

import logging

logger = logging.getLogger("clawhermes.lark.scope_manager")

# ---------------------------------------------------------------------------
# 敏感 scope — 不在卡片中展示
# ---------------------------------------------------------------------------

SENSITIVE_SCOPES: set[str] = {
    "contact:user.base:readonly",
    "contact:user.employee_id:readonly",
    "im:message",
    "im:chat",
}

# ---------------------------------------------------------------------------
# 已知的 scope 列表（来自飞书开放平台）
# ---------------------------------------------------------------------------

KNOWN_SCOPES: dict[str, str] = {
    # 通讯录
    "contact:user.base:readonly": "读取用户基本信息",
    "contact:user.employee_id:readonly": "读取用户 employee_id",
    "contact:user.phone:readonly": "读取用户手机号",
    "contact:user.email:readonly": "读取用户邮箱",
    # 消息
    "im:message": "发送和接收消息",
    "im:chat": "获取群聊信息",
    "im:message:send_as_bot": "以机器人身份发送消息",
    "im:message:reaction": "消息表情回应",
    "im:resource": "获取消息中的图片/文件",
    # 日历
    "calendar:calendar:readonly": "读取日历",
    "calendar:calendar:write": "创建/修改日历",
    "calendar:event:readonly": "读取日历事件",
    "calendar:event:write": "创建/修改日历事件",
    # 文档
    "doc:document:readonly": "读取文档",
    "doc:document:write": "创建/修改文档",
    "docx:document:readonly": "读取新版文档",
    "docx:document:write": "创建/修改新版文档",
    # 表格
    "sheet:readonly": "读取电子表格",
    "sheet:write": "创建/修改电子表格",
    "drive:drive:readonly": "读取云空间",
    "drive:drive:write": "上传/修改云空间文件",
    # 知识库
    "wiki:wiki:readonly": "读取知识库",
    "wiki:wiki:write": "创建/修改知识库",
    # 任务
    "task:task:readonly": "读取任务",
    "task:task:write": "创建/修改任务",
    # 审批
    "approval:approval:readonly": "读取审批",
    "approval:approval:write": "创建审批",
    # 多维表格
    "bitable:app:readonly": "读取多维表格",
    "bitable:app:write": "创建/修改多维表格",
    # 邮件
    "mail:mail:readonly": "读取邮件",
    "mail:mail:write": "发送邮件",
    # 搜索
    "search:search:readonly": "搜索",
    # 视频会议
    "vc:meeting:readonly": "读取会议信息",
    "vc:meeting:write": "创建/修改会议",
    # 其他
    "offline_access": "离线访问（获取 refresh_token）",
    "openid": "获取用户 open_id",
    "profile": "获取用户基本信息",
    "email": "获取用户邮箱",
    "phone": "获取用户手机号",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def filter_sensitive_scopes(scopes: list[str]) -> list[str]:
    """过滤敏感 scope，仅返回可在卡片中展示的."""
    return [s for s in scopes if s not in SENSITIVE_SCOPES]


def get_scope_description(scope: str) -> str:
    """获取 scope 的中文描述."""
    return KNOWN_SCOPES.get(scope, scope)


def get_scopes_descriptions(scopes: list[str]) -> dict[str, str]:
    """批量获取 scope 描述."""
    return {s: get_scope_description(s) for s in scopes}


def check_scope_satisfied(
    required: list[str],
    granted: list[str],
) -> tuple[bool, list[str]]:
    """
    检查已授权的 scope 是否满足需求.

    Returns:
        (satisfied, missing_scopes)
    """
    granted_set = set(granted)
    missing = [s for s in required if s not in granted_set]
    return len(missing) == 0, missing


def categorize_scopes(scopes: list[str]) -> dict[str, list[str]]:
    """
    将 scope 按类别分组.

    Returns:
        {"通讯录": [...], "消息": [...], "日历": [...], ...}
    """
    categories: dict[str, list[str]] = {
        "通讯录": [], "消息": [], "日历": [], "文档": [],
        "表格": [], "知识库": [], "任务": [], "其他": [],
    }

    prefix_map = {
        "contact:": "通讯录", "im:": "消息",
        "calendar:": "日历", "doc:": "文档", "docx:": "文档",
        "sheet:": "表格", "drive:": "表格",
        "wiki:": "知识库", "task:": "任务",
    }

    for s in scopes:
        cat = "其他"
        for prefix, category in prefix_map.items():
            if s.startswith(prefix):
                cat = category
                break
        categories[cat].append(s)

    return {k: v for k, v in categories.items() if v}
