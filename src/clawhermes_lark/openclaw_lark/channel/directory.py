"""
目录查询 — 对齐 openclaw-lark src/channel/directory.ts

飞书用户和群组目录查询：
  - list_peers: 查询组织架构中的用户
  - list_groups: 查询 Bot 所在的群聊列表
  - 支持分页和搜索筛选
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("clawhermes.lark.directory")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class DirectoryPeer:
    """目录中的用户."""
    __slots__ = ("id", "name", "kind")

    def __init__(self, id: str, name: str = "", kind: str = "user"):
        self.id = id
        self.name = name
        self.kind = kind


class DirectoryGroup:
    """目录中的群聊."""
    __slots__ = ("id", "name", "kind")

    def __init__(self, id: str, name: str = "", kind: str = "group"):
        self.id = id
        self.name = name
        self.kind = kind


# ---------------------------------------------------------------------------
# Peers (用户)
# ---------------------------------------------------------------------------


async def list_feishu_directory_peers(
    client,
    query: str = "",
    limit: int = 50,
) -> list[DirectoryPeer]:
    """
    查询组织架构中的用户（搜索）.

    Args:
        client: lark_oapi.Client 实例
        query: 搜索关键词（姓名/邮箱）
        limit: 返回数量上限
    """
    try:
        from lark_oapi.api.contact.v3 import (
            SearchUserRequest,
            SearchUserRequestBody,
        )

        body = SearchUserRequestBody.builder() \
            .query(query or "") \
            .page_size(min(limit, 100)) \
            .build()
        req = SearchUserRequest.builder() \
            .request_body(body) \
            .build()

        resp = client.contact.v3.user.search(req)
        if resp.code != 0 or not resp.data:
            return []

        items = getattr(resp.data, "items", []) or []
        result: list[DirectoryPeer] = []
        for user in items:
            result.append(DirectoryPeer(
                id=getattr(user, "open_id", "") or "",
                name=getattr(user, "name", "") or "",
                kind="user",
            ))
        return result[:limit]

    except Exception:
        logger.debug("list_feishu_directory_peers failed", exc_info=True)
        return []


async def list_feishu_directory_peers_live(
    client,
    query: str = "",
    limit: int = 50,
) -> list[DirectoryPeer]:
    """
    实时搜索组织架构用户（带 debounce 的实时搜索）.

    与 list_feishu_directory_peers 相同，但在 UI 中用于即时搜索场景.
    """
    return await list_feishu_directory_peers(client, query=query, limit=limit)


# ---------------------------------------------------------------------------
# Groups (群聊)
# ---------------------------------------------------------------------------


async def list_feishu_directory_groups(
    client,
    query: str = "",
    limit: int = 50,
) -> list[DirectoryGroup]:
    """
    查询 Bot 所在的群聊列表.

    Args:
        client: lark_oapi.Client 实例
        query: 群名称搜索关键词
        limit: 返回数量上限
    """
    try:
        from lark_oapi.api.im.v1 import ListChatRequest

        builder = ListChatRequest.builder() \
            .page_size(min(limit, 100))
        if query:
            builder = builder.query(query)
        req = builder.build()

        resp = client.im.v1.chat.list(req)
        if resp.code != 0 or not resp.data:
            return []

        items = getattr(resp.data, "items", []) or []
        result: list[DirectoryGroup] = []
        for chat in items:
            result.append(DirectoryGroup(
                id=getattr(chat, "chat_id", "") or "",
                name=getattr(chat, "name", "") or "",
                kind="group",
            ))
        return result[:limit]

    except Exception:
        logger.debug("list_feishu_directory_groups failed", exc_info=True)
        return []


async def list_feishu_directory_groups_live(
    client,
    query: str = "",
    limit: int = 50,
) -> list[DirectoryGroup]:
    """实时搜索群聊."""
    return await list_feishu_directory_groups(client, query=query, limit=limit)
