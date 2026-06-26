"""
ClawHermes-Lark — LarkClient 统一 SDK 管理器

对齐 larksuite/openclaw-lark 的 LarkClient 模式:
  - from_credentials()  — 凭证 → 客户端
  - probe()             — 连接探测 (bot/v3/info)
  - get_bot_identity()  — 获取机器人身份
  - Token 自动管理      — lark-oapi 内置

使用方式:
    client = LarkClient.from_credentials(app_id, app_secret, domain="feishu")
    identity = await client.get_bot_identity()
    print(f"Connected as {identity.name} ({identity.open_id})")
"""
from __future__ import annotations

from dataclasses import dataclass


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

    封装 lark-oapi SDK, 负责:
      - 客户端生命周期管理
      - 连接探测 (probe)
      - 机器人身份获取
      - Domain 映射 (feishu / lark)

    对齐 larksuite/openclaw-lark 的 LarkClient 模式.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        domain: str = "feishu",
    ):
        import lark_oapi as lark

        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain

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
    ) -> LarkClient:
        """从凭证创建客户端"""
        return cls(app_id=app_id, app_secret=app_secret, domain=domain)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe_sync(self) -> BotIdentity | None:
        """同步连接探测 — 调用 bot/v3/info API

        Returns:
            BotIdentity 若成功, None 若失败.
        """
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
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_domain(domain: str):
        """域名 → lark-oapi Domain 枚举"""
        import lark_oapi as lark

        domain_lower = domain.lower().strip()
        domain_map = {
            "feishu": lark.Domain.FEISHU,
            "lark": lark.Domain.LARK,
        }
        result = domain_map.get(domain_lower)
        if result is not None:
            return result
        raise ValueError(
            f"不支持的 domain: {domain!r}, 可选: feishu, lark"
        )
