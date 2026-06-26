"""
ClawHermes-Lark — LarkClient 统一 SDK 管理器

对齐 larksuite/openclaw-lark 的 LarkClient 模式:
  - from_credentials()  — 凭证 → 客户端
  - from_account()      — 从 LarkAccount 创建
  - probe()             — 连接探测 (bot/v3/info)
  - get_bot_identity()  — 获取机器人身份
  - Token 自动管理      — lark-oapi 内置
  - 多账户支持          — 按 accountId 缓存实例

使用方式:
    client = LarkClient.from_credentials(app_id, app_secret, domain="feishu")
    identity = await client.get_bot_identity()
    print(f"Connected as {identity.name} ({identity.open_id})")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("clawhermes.lark.client")

# ---------------------------------------------------------------------------
# Instance cache
# ---------------------------------------------------------------------------

_cache: dict[str, "LarkClient"] = {}


@dataclass
class BotIdentity:
    """飞书机器人身份 — 对齐 openclaw-lark FeishuProbeResult"""
    open_id: str
    name: str = ""
    tenant_key: str = ""

    @property
    def bot_name(self) -> str:
        return self.name or self.open_id


class LarkClient:
    """飞书 Lark 客户端统一管理器

    封装 lark-oapi SDK，负责:
      - 客户端生命周期管理
      - 连接探测 (probe)
      - 机器人身份获取
      - Domain 映射 (feishu / lark)
      - 多账户实例缓存

    对齐 larksuite/openclaw-lark 的 LarkClient 模式.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        domain: str = "feishu",
        account_id: str = "default",
        brand: str = "feishu",
    ):
        import lark_oapi as lark

        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self.account_id = account_id
        self.brand = brand

        self._lark_domain = self._resolve_domain(domain)
        self._client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .domain(self._lark_domain) \
            .build()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_credentials(
        cls,
        app_id: str,
        app_secret: str,
        domain: str = "feishu",
        account_id: str = "default",
    ) -> LarkClient:
        """从凭证创建客户端（不缓存）"""
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            domain=domain,
            account_id=account_id,
            brand=domain,
        )

    @classmethod
    def from_account(cls, account: Any) -> LarkClient:
        """从 LarkAccount 创建客户端（含缓存）"""
        aid = getattr(account, "account_id", "default")

        existing = _cache.get(aid)
        if existing and existing.app_id == account.app_id:
            return existing

        if existing and existing.app_id != account.app_id:
            logger.info("Credentials changed, disposing stale instance: %s", aid)
            existing.dispose()

        instance = cls(
            app_id=account.app_id or "",
            app_secret=account.app_secret or "",
            domain=getattr(account, "brand", "feishu"),
            account_id=aid,
            brand=getattr(account, "brand", "feishu"),
        )
        _cache[aid] = instance
        return instance

    @classmethod
    def get(cls, account_id: str = "default") -> LarkClient | None:
        """按 accountId 查找缓存实例"""
        return _cache.get(account_id)

    @classmethod
    async def clear_cache(cls, account_id: str | None = None) -> None:
        """清除缓存实例"""
        if account_id is not None:
            inst = _cache.pop(account_id, None)
            if inst:
                inst.dispose()
        else:
            for inst in list(_cache.values()):
                inst.dispose()
            _cache.clear()

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe_sync(self) -> BotIdentity | None:
        """同步连接探测 — 调用 bot/v3/info API"""
        import lark_oapi as lark
        from lark_oapi.api.verification.v1 import GetBotInfoRequest

        try:
            request = GetBotInfoRequest()
            response = self._client.verification.v1.bot_info.get(request)
            if not response.success():
                return None
            data = response.data
            if data is None:
                return None
            return BotIdentity(
                open_id=getattr(data, "open_id", "") or "",
                name=getattr(data, "name", "") or "",
                tenant_key=getattr(data, "tenant_key", "") or "",
            )
        except Exception:
            return None

    async def probe(self) -> BotIdentity | None:
        """异步连接探测"""
        return self.probe_sync()

    # ------------------------------------------------------------------
    # Bot Identity
    # ------------------------------------------------------------------

    def get_bot_identity_sync(self) -> BotIdentity:
        """获取机器人身份 — 失败抛出异常"""
        identity = self.probe_sync()
        if identity is None:
            raise ConnectionError(
                f"无法连接到飞书 API (domain={self.domain}), "
                f"请检查 App ID / App Secret"
            )
        return identity

    async def get_bot_identity(self) -> BotIdentity:
        """异步获取机器人身份"""
        return self.get_bot_identity_sync()

    # ------------------------------------------------------------------
    # Raw SDK client
    # ------------------------------------------------------------------

    @property
    def sdk(self):
        """获取底层 lark_oapi.Client 实例"""
        return self._client

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_domain(domain: str):
        """域名 → lark-oapi Domain 枚举"""
        import lark_oapi as lark

        domain_lower = domain.lower().strip()
        domain_map = {
            "feishu": lark.FEISHU_DOMAIN,
            "lark": lark.LARK_DOMAIN,
        }
        result = domain_map.get(domain_lower)
        if result is not None:
            return result
        if domain_lower == "lark_suite":
            return lark.LARK_DOMAIN
        raise ValueError(
            f"不支持的 domain: {domain!r}, 可选: feishu, lark"
        )

    def dispose(self) -> None:
        """清理客户端资源"""
        self._client = None
