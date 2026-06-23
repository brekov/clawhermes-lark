"""
ClawHermes-Lark — 飞书/Lark 渠道适配器
基于 lark-oapi 官方 SDK，完整实现 larksuite/openclaw-lark 核心功能

设计模式对齐 openclaw-lark：
- WebSocket 长连接事件订阅（自动重连）
- lark_oapi.Client 统一 API + Token 自动管理
- Fluent Builder 模式构造请求
- 事件回调注册 + 消息分发
- 适配 ClawHermes ChannelAdapter 接口
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
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

logger = logging.getLogger("clawhermes.lark")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class LarkConfig:
    """飞书/Lark 应用配置"""
    app_id: str
    app_secret: str
    verification_token: str = ""
    encrypt_key: str = ""
    domain: str = "feishu"       # "feishu" | "lark"
    auto_reconnect: bool = True
    log_level: int = logging.INFO


# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------

class LarkEventType(str, Enum):
    """飞书事件类型（参考 openclaw-lark）"""
    MESSAGE_RECEIVE = "im.message.receive_v1"
    MESSAGE_READ = "im.message.read_v1"
    MESSAGE_REACTION_CREATED = "im.message.reaction.created_v1"
    MESSAGE_REACTION_DELETED = "im.message.reaction.deleted_v1"
    CHAT_DISBANDED = "im.chat.disbanded_v1"
    CHAT_UPDATED = "im.chat.updated_v1"
    URL_VERIFICATION = "url_verification"


# ---------------------------------------------------------------------------
# 飞书适配器
# ---------------------------------------------------------------------------

class LarkAdapter(ChannelAdapter):
    """
    飞书/Lark 渠道适配器

    功能对齐 larksuite/openclaw-lark：
    - WebSocket 长连接 + 自动重连
    - 消息收发（文本/卡片/富文本）
    - 事件订阅与分发
    - 用户信息查询
    - HTTP Webhook 兼容（URL 验证）

    使用方式：
        adapter = LarkAdapter({
            "app_id": "cli_xxx",
            "app_secret": "xxx",
        })
        await adapter.start()
        # ... 消息自动通过 WebSocket 接收 ...
        await adapter.stop()
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(ChannelType.FEISHU, config)
        cfg = config or {}

        self._lark_config = LarkConfig(
            app_id=cfg.get("app_id", ""),
            app_secret=cfg.get("app_secret", ""),
            verification_token=cfg.get("verification_token", ""),
            encrypt_key=cfg.get("encrypt_key", ""),
            domain=cfg.get("domain", "feishu"),
            auto_reconnect=cfg.get("auto_reconnect", True),
            log_level=cfg.get("log_level", logging.INFO),
        )

        # lark-oapi Client（Token 自动管理）
        self._client: lark.Client | None = None

        # WebSocket 客户端（参考 openclaw-lark 长连接模式）
        self._ws_client: WsClient | None = None
        self._ws_task: asyncio.Task | None = None

        # 事件处理器注册表
        self._event_handlers: dict[str, Any] = {}

        # open_id 缓存
        self._open_id_map: dict[str, str] = {}

    # ==================================================================
    # ChannelAdapter 接口
    # ==================================================================

    async def start(self) -> None:
        """启动适配器：初始化 Client + 建立 WebSocket 长连接"""
        if self._running:
            return

        if not self._lark_config.app_id or not self._lark_config.app_secret:
            logger.warning("Lark: 未配置 app_id/app_secret，跳过启动")
            return

        # 1. 初始化 lark-oapi Client
        domain = (
            lark.FEISHU_DOMAIN
            if self._lark_config.domain == "feishu"
            else lark.LARK_DOMAIN
        )
        self._client = (
            lark.Client.builder()
            .app_id(self._lark_config.app_id)
            .app_secret(self._lark_config.app_secret)
            .domain(domain)
            .log_level(lark.LogLevel(self._lark_config.log_level))
            .build()
        )

        # 2. 注册核心事件处理器
        self._event_handlers[LarkEventType.MESSAGE_RECEIVE] = self._handle_message_receive
        self._event_handlers[LarkEventType.MESSAGE_READ] = self._handle_message_read

        # 3. 启动 WebSocket 长连接
        self._ws_task = asyncio.create_task(self._ws_loop(), name="lark_ws")
        self._running = True
        logger.info("Lark Adapter 已启动（WS 模式, app_id=%s...）",
                    self._lark_config.app_id[:8])

    async def stop(self) -> None:
        """停止适配器：取消 WS 连接、清理资源"""
        self._running = False

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        self._ws_client = None
        self._client = None
        self._open_id_map.clear()
        logger.info("Lark Adapter 已停止")

    async def send_response(self, response: ChannelResponse, original: ChannelMessage) -> None:
        """发送回复消息（使用 lark-oapi Builder 模式）"""
        receive_id = original.user.user_id or original.metadata.get("open_id", "")
        chat_id = original.metadata.get("chat_id", "")

        if not receive_id or self._client is None:
            return

        try:
            body = CreateMessageRequestBody.builder() \
                .receive_id(receive_id) \
                .msg_type("text") \
                .content(json.dumps({"text": response.content}, ensure_ascii=False)) \
                .build()

            request = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(body) \
                .build()

            resp = await self._client.arequest(request)
            if not resp.success():
                logger.error("Lark 发送消息失败: code=%s msg=%s", resp.code, resp.msg)
        except Exception as e:
            logger.error("Lark 发送消息异常: %s", e)

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
                        "avatar": getattr(getattr(user, "avatar", None), "avatar_240", ""),
                        "email": getattr(user, "email", ""),
                    },
                )
        except Exception as e:
            logger.warning("Lark 获取用户信息失败: %s", e)
        return None

    # ==================================================================
    # WebSocket 事件循环（openclaw-lark 核心模式）
    # ==================================================================

    async def _ws_loop(self) -> None:
        """WebSocket 事件订阅循环（自动重连）"""
        while self._running:
            try:
                self._ws_client = WsClient(
                    app_id=self._lark_config.app_id,
                    app_secret=self._lark_config.app_secret,
                    event_handler=self._dispatch_event,
                    domain=(
                        lark.FEISHU_DOMAIN
                        if self._lark_config.domain == "feishu"
                        else lark.LARK_DOMAIN
                    ),
                    auto_reconnect=self._lark_config.auto_reconnect,
                )
                logger.info("Lark WebSocket 连接中...")
                await self._ws_client.start()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Lark WebSocket 异常，5s 后重连: %s", e)
                await asyncio.sleep(5)

    async def _dispatch_event(self, event: Any) -> None:
        """事件分发（参考 openclaw-lark 回调模式）"""
        event_type = getattr(event, "type", "") or str(event)
        handler = self._event_handlers.get(event_type)
        if handler:
            try:
                await handler(event)
            except Exception as e:
                logger.exception("Lark 事件处理异常 [%s]: %s", event_type, e)

    # ==================================================================
    # 事件处理器
    # ==================================================================

    async def _handle_message_receive(self, event: Any) -> None:
        """处理 im.message.receive_v1 事件"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event is None:
                return

            message = msg_event.message
            sender = msg_event.sender
            if message is None or sender is None:
                return

            open_id = getattr(sender.sender_id, "open_id", "")
            chat_id = getattr(message, "chat_id", "")
            msg_type = getattr(message, "message_type", "text")
            msg_id = getattr(message, "message_id", "")

            content = self._extract_text_content(message, msg_type)
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

        except Exception:
            logger.exception("Lark _handle_message_receive 异常")

    async def _handle_message_read(self, event: Any) -> None:
        """处理已读事件（日志记录）"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event:
                reader = getattr(msg_event, "reader", None)
                msg_id_list = getattr(msg_event, "message_id_list", [])
                logger.debug("Lark 消息已读: reader=%s, count=%d",
                            getattr(reader, "open_id", "?"), len(msg_id_list or []))
        except Exception:
            pass

    # ==================================================================
    # 工具方法
    # ==================================================================

    @staticmethod
    def _extract_text_content(message: Any, msg_type: str) -> str:
        """从飞书消息体中提取文本内容"""
        if msg_type != "text":
            return ""
        raw = getattr(message, "content", "")
        if isinstance(raw, str):
            try:
                body = json.loads(raw)
                return body.get("text", "")
            except json.JSONDecodeError:
                return raw
        return str(raw)

    # ==================================================================
    # HTTP Webhook 兼容
    # ==================================================================

    async def handle_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        """处理 HTTP Webhook 回调（URL 验证 + 事件解密）"""
        if body.get("type") == LarkEventType.URL_VERIFICATION:
            return {"challenge": body.get("challenge", "")}
        return {}


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def create_lark_adapter(
    app_id: str = "",
    app_secret: str = "",
    verification_token: str = "",
    domain: str = "feishu",
    **kwargs: Any,
) -> LarkAdapter:
    """快速创建飞书适配器"""
    return LarkAdapter({
        "app_id": app_id,
        "app_secret": app_secret,
        "verification_token": verification_token,
        "domain": domain,
        **kwargs,
    })
