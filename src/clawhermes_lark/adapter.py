"""
ClawHermes-Lark — 飞书渠道适配器（基于 lark-oapi 官方 SDK）

参考 larksuite/openclaw-lark 设计模式：
- WebSocket 长连接事件订阅（实时消息接收）
- lark_oapi.Client 统一 API 调用（Token 自动管理）
- Fluent Builder 模式构造请求
- 事件回调注册模式分发消息
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.ws import Client as WsClient

from clawhermes.channel.adapter import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResponse,
    ChannelType,
    ChannelUser,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class FeishuConfig:
    """飞书应用配置（兼容 lark-oapi）"""
    app_id: str
    app_secret: str
    verification_token: str = ""
    encrypt_key: str = ""
    base_url: str = "https://open.feishu.cn"
    webhook_path: str = "/feishu/webhook"


# ---------------------------------------------------------------------------
# 飞书适配器
# ---------------------------------------------------------------------------

class FeishuAdapter(ChannelAdapter):
    """
    飞书渠道适配器（基于 lark-oapi）

    参考 larksuite/openclaw-lark 的设计：

    1. **WebSocket 长连接** — 通过 lark_oapi.ws.Client 订阅事件，
       避免轮询，降低延迟
    2. **Client 统一入口** — 所有 API 调用通过 lark_oapi.Client，
       Token 自动刷新
    3. **Builder 模式** — 请求构造使用 Fluent Builder，类型安全
    4. **事件回调** — 注册 im.message.receive_v1 回调，解耦消息处理

    使用方式：
    1. 在飞书开放平台创建自建应用
    2. 订阅 im.message.receive_v1 事件
    3. 配置环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(ChannelType.FEISHU, config)
        cfg = config or {}

        self._feishu_config = FeishuConfig(
            app_id=cfg.get("app_id", ""),
            app_secret=cfg.get("app_secret", ""),
            verification_token=cfg.get("verification_token", ""),
            encrypt_key=cfg.get("encrypt_key", ""),
            base_url=cfg.get("base_url", "https://open.feishu.cn"),
            webhook_path=cfg.get("webhook_path", "/feishu/webhook"),
        )

        # lark-oapi Client（Token 自动管理）
        self._client: lark.Client | None = None

        # WebSocket 客户端（实时事件订阅，参考 openclaw-lark）
        self._ws_client: WsClient | None = None
        self._ws_task: asyncio.Task | None = None

        # 消息 → open_id 映射
        self._open_id_map: dict[str, str] = {}

        # 事件处理器
        self._event_handlers: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # ChannelAdapter 接口
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动飞书适配器：初始化 Client + 启动 WebSocket 事件订阅"""
        if self._running:
            return

        if not self._feishu_config.app_id or not self._feishu_config.app_secret:
            logger.warning("Feishu Adapter: 未配置 app_id/app_secret，跳过启动")
            return

        # 1. 初始化 lark-oapi Client
        self._client = (
            lark.Client.builder()
            .app_id(self._feishu_config.app_id)
            .app_secret(self._feishu_config.app_secret)
            .domain(
                lark.FEISHU_DOMAIN
                if "feishu" in self._feishu_config.base_url
                else lark.LARK_DOMAIN
            )
            .build()
        )

        # 2. 注册事件处理器（参考 openclaw-lark 事件回调模式）
        self._event_handlers["im.message.receive_v1"] = self._on_message_event

        # 3. 启动 WebSocket 长连接
        self._ws_task = asyncio.create_task(self._ws_loop(), name="feishu_ws")
        self._running = True
        logger.info("Feishu Adapter 已启动 (lark-oapi WS 模式)")

    async def stop(self) -> None:
        """停止适配器"""
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        self._open_id_map.clear()
        self._client = None
        logger.info("Feishu Adapter 已停止")

    async def send_response(self, response: ChannelResponse, original: ChannelMessage) -> None:
        """发送回复消息（使用 lark-oapi Builder 模式）"""
        open_id = original.user.user_id
        if not open_id or self._client is None:
            return

        body = CreateMessageRequestBody.builder() \
            .receive_id(open_id) \
            .msg_type("text") \
            .content(json.dumps({"text": response.content}, ensure_ascii=False)) \
            .build()

        request = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(body) \
            .build()

        try:
            resp = await self._client.arequest(request)
            if not resp.success():
                logger.error("Feishu 发送消息失败: code=%s msg=%s", resp.code, resp.msg)
        except Exception as e:
            logger.error("Feishu 发送消息异常: %s", e)

    async def get_user_info(self, user_id: str) -> ChannelUser | None:
        """获取飞书用户信息"""
        if self._client is None:
            return None
        try:
            from lark_oapi.api.contact.v3 import GetUserRequest
            request = GetUserRequest.builder() \
                .user_id(user_id) \
                .user_id_type("open_id") \
                .build()
            resp = await self._client.arequest(request)
            if resp.success() and resp.data:
                user = resp.data.user
                return ChannelUser(
                    user_id=user_id,
                    display_name=getattr(user, "name", user_id),
                    metadata={
                        "avatar": getattr(user, "avatar", {}).get("avatar_240", ""),
                        "email": getattr(user, "email", ""),
                    },
                )
        except Exception as e:
            logger.warning("Feishu 获取用户信息失败: %s", e)
        return None

    # ------------------------------------------------------------------
    # WebSocket 事件循环（参考 openclaw-lark）
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """WebSocket 事件订阅循环（自动重连）"""
        while self._running:
            try:
                self._ws_client = WsClient(
                    app_id=self._feishu_config.app_id,
                    app_secret=self._feishu_config.app_secret,
                    event_handler=self._dispatch_event,
                )
                logger.info("Feishu WebSocket 连接中...")
                await self._ws_client.start()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Feishu WebSocket 异常，5s 后重连: %s", e)
                await asyncio.sleep(5)

    async def _dispatch_event(self, event: Any) -> None:
        """事件分发（参考 openclaw-lark 事件回调）"""
        event_type = getattr(event, "type", "") or ""
        handler = self._event_handlers.get(event_type)
        if handler:
            await handler(event)

    async def _on_message_event(self, event: Any) -> None:
        """处理 im.message.receive_v1 事件"""
        try:
            msg_event = event.event
            if msg_event is None:
                return

            message = msg_event.message
            sender = msg_event.sender
            if message is None or sender is None:
                return

            open_id = getattr(sender.sender_id, "open_id", "")
            chat_id = message.chat_id
            msg_type = getattr(message, "message_type", "text")
            msg_id = message.message_id

            # 提取文本内容
            content = ""
            if msg_type == "text":
                body = json.loads(message.content) if isinstance(message.content, str) else {}
                content = body.get("text", "")

            if not content:
                return

            channel_msg = ChannelMessage(
                message_id=msg_id,
                channel_type=ChannelType.FEISHU,
                user=ChannelUser(user_id=open_id),
                content=content,
                session_id=chat_id,
                metadata={
                    "chat_id": chat_id,
                    "msg_type": msg_type,
                    "open_id": open_id,
                },
            )
            self._open_id_map[chat_id] = open_id
            self._dispatch_message(channel_msg)

        except Exception as e:
            logger.exception("Feishu 消息事件处理异常: %s", e)

    # ------------------------------------------------------------------
    # Webhook 事件处理（HTTP 方式，兼容无 WS 环境）
    # ------------------------------------------------------------------

    async def handle_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        """处理飞书 HTTP Webhook 回调（URL 验证 + 事件）"""
        # URL 验证
        if body.get("type") == "url_verification":
            challenge = body.get("challenge", "")
            return {"challenge": challenge}

        # 事件解密（如有需要）
        # lark-oapi 的 EventDispatcherHandler 可处理加解密
        return {}


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def create_feishu_adapter(
    app_id: str = "",
    app_secret: str = "",
    verification_token: str = "",
    **kwargs: Any,
) -> FeishuAdapter:
    """快速创建飞书适配器"""
    return FeishuAdapter({
        "app_id": app_id,
        "app_secret": app_secret,
        "verification_token": verification_token,
        **kwargs,
    })
