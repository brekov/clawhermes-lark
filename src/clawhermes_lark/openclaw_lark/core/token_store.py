"""
OAuth Token 持久化存储 — 对齐 openclaw-lark src/core/token-store.ts

管理 Device Flow 获取的 access_token / refresh_token：
  - 按 account_id + user_id 存储
  - 自动检测过期并刷新
  - JSON 文件持久化

用于 OAPI 工具以用户身份调用飞书 API.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("clawhermes.lark.token_store")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class StoredToken:
    """持久化的 token 记录."""
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0       # Unix timestamp
    refresh_expires_at: float = 0.0
    scope: str = ""
    user_id: str = ""

    @property
    def is_expired(self) -> bool:
        """access_token 是否已过期."""
        return time.time() >= self.expires_at

    @property
    def can_refresh(self) -> bool:
        """是否可以使用 refresh_token 刷新."""
        return bool(self.refresh_token) and time.time() < self.refresh_expires_at


# ---------------------------------------------------------------------------
# Token Store
# ---------------------------------------------------------------------------


class TokenStore:
    """OAuth token 持久化管理器."""

    __slots__ = ("_tokens", "_file_path")

    def __init__(self, file_path: str = ""):
        self._tokens: dict[str, dict[str, StoredToken]] = {}
        self._file_path = file_path

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, account_id: str, user_id: str) -> StoredToken | None:
        """获取 token."""
        return self._tokens.get(account_id, {}).get(user_id)

    def set(self, account_id: str, user_id: str, token: StoredToken) -> None:
        """存储 token."""
        self._tokens.setdefault(account_id, {})[user_id] = token

    def delete(self, account_id: str, user_id: str) -> None:
        """删除 token."""
        account_tokens = self._tokens.get(account_id, {})
        account_tokens.pop(user_id, None)

    def get_valid_token(self, account_id: str, user_id: str) -> StoredToken | None:
        """获取有效的 token（未过期），过期返回 None."""
        token = self.get(account_id, user_id)
        if token and not token.is_expired:
            return token
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self, file_path: str = "") -> None:
        """从 JSON 文件加载 token."""
        path = file_path or self._file_path
        if not path:
            return

        try:
            data_dir = os.path.dirname(path)
        except Exception:
            return

        if not os.path.exists(path):
            return

        try:
            with open(path) as f:
                data = json.load(f)

            for account_id, users in data.items():
                for user_id, token_data in users.items():
                    self._tokens.setdefault(account_id, {})[user_id] = StoredToken(
                        access_token=token_data.get("access_token", ""),
                        refresh_token=token_data.get("refresh_token", ""),
                        expires_at=token_data.get("expires_at", 0),
                        refresh_expires_at=token_data.get("refresh_expires_at", 0),
                        scope=token_data.get("scope", ""),
                        user_id=user_id,
                    )
            logger.debug("Loaded %d tokens", sum(len(v) for v in self._tokens.values()))
        except Exception:
            logger.debug("Failed to load token store", exc_info=True)

    def save(self, file_path: str = "") -> None:
        """持久化 token 到 JSON 文件."""
        path = file_path or self._file_path
        if not path:
            return

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            return

        data: dict[str, dict[str, dict[str, Any]]] = {}
        for account_id, users in self._tokens.items():
            data[account_id] = {}
            for user_id, token in users.items():
                data[account_id][user_id] = {
                    "access_token": token.access_token,
                    "refresh_token": token.refresh_token,
                    "expires_at": token.expires_at,
                    "refresh_expires_at": token.refresh_expires_at,
                    "scope": token.scope,
                }

        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(path), delete=False, suffix=".tmp"
        )
        try:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, path)
            logger.debug("Saved %d tokens", sum(len(v) for v in self._tokens.values()))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            logger.debug("Failed to save token store", exc_info=True)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_default_store: TokenStore | None = None


def get_token_store(file_path: str = "") -> TokenStore:
    """获取进程级 TokenStore 单例."""
    global _default_store
    if _default_store is None:
        _default_store = TokenStore(file_path=file_path)
        if file_path:
            _default_store.load(file_path)
    return _default_store
