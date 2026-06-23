"""
ClawHermes-Lark — 飞书/Lark 渠道适配器

分层架构：
  Layer 1: lark-oapi 官方 SDK — Token 管理、认证、租户操作、事件订阅
  Layer 2: Hermes vendor utils  — 消息解析、Markdown 转换、@提及标准化
  Layer 3: ChannelAdapter — ClawHermes 统一适配器接口

设计模式对齐 larksuite/openclaw-lark：
  - WebSocket 长连接事件订阅（自动重连）
  - lark_oapi.Client 统一 API + Token 自动管理
  - 事件回调注册 + 消息分发
  - Hermes 生产级消息引擎复用
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import lark_oapi as lark
from lark_oapi.api.contact.v3 import ListUserRequest
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
)

from clawhermes.channel.adapter import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResponse,
    ChannelType,
    ChannelUser,
)

# Hermes vendor — 消息解析/格式化引擎（5512 行生产级代码）
from clawhermes_lark.hermes_vendor import (
    _build_markdown_post_payload,
    _build_markdown_post_rows,
    _build_mentions_map,
    _escape_markdown_text,
    _extract_mention_ids,
    _strip_markdown_to_plain_text,
    FeishuMentionRef,
    normalize_feishu_message,
    parse_feishu_post_payload,
)

logger = logging.getLogger("clawhermes.lark")


# ============================================================================
# 配置
# ============================================================================

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
    max_retries: int = 3
    retry_delay: float = 1.0


# ============================================================================
# 事件类型
# ============================================================================

class LarkEventType(str, Enum):
    """飞书事件类型（参考 openclaw-lark）"""
    MESSAGE_RECEIVE = "im.message.receive_v1"
    MESSAGE_READ = "im.message.read_v1"
    MESSAGE_REACTION_CREATED = "im.message.reaction.created_v1"
    MESSAGE_REACTION_DELETED = "im.message.reaction.deleted_v1"
    CHAT_DISBANDED = "im.chat.disbanded_v1"
    CHAT_UPDATED = "im.chat.updated_v1"
    URL_VERIFICATION = "url_verification"


# ============================================================================
# LarkAdapter — 飞书渠道适配器
# ============================================================================

class LarkAdapter(ChannelAdapter):
    """
    飞书/Lark 渠道适配器

    Layer 1 (lark-oapi):
      - lark.Client: Token 自动管理 + 统一 API 入口
      - lark.ws.Client: WebSocket 长连接 + 自动重连
      - API 调用: 发送消息、获取用户信息、上传文件

    Layer 2 (Hermes vendor):
      - 消息解析: parse_feishu_post_payload, normalize_feishu_message
      - Markdown 转换: _build_markdown_post_payload, _escape_markdown_text
      - @提及提取: _build_mentions_map, _extract_mention_ids

    Layer 3 (ChannelAdapter):
      - 统一接口: start/stop/send_response/get_user_info
      - 消息分发: on_message → _dispatch_message
    """

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

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
            max_retries=int(cfg.get("max_retries", 3)),
            retry_delay=float(cfg.get("retry_delay", 1.0)),
        )

        # Layer 1: lark-oapi Client（Token 自动管理）
        self._client: lark.Client | None = None

        # Layer 1: WebSocket 客户端（参考 openclaw-lark 长连接模式）
        self._ws_client: lark.ws.Client | None = None
        self._ws_task: asyncio.Task | None = None

        # 事件处理器注册表
        self._event_handlers: dict[str, Callable] = {}

        # open_id 缓存 (chat_id → open_id)
        self._session_users: dict[str, str] = {}

        # 重连控制
        self._should_reconnect = True
        self._ws_error_count = 0
        self._max_ws_errors = 10

    # ==================================================================
    # ChannelAdapter 接口 — start / stop
    # ==================================================================

    async def start(self) -> None:
        """启动适配器：初始化 Client + 建立 WebSocket 长连接"""
        if self._running:
            return

        if not self._lark_config.app_id or not self._lark_config.app_secret:
            logger.warning("Lark: 未配置 app_id/app_secret，跳过启动")
            return

        # 初始化 lark-oapi Client
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

        logger.info(
            "Lark client initialized: app_id=%s domain=%s",
            self._lark_config.app_id[:8] + "***",
            self._lark_config.domain,
        )

        # 注册核心事件处理器
        self._event_handlers[LarkEventType.MESSAGE_RECEIVE] = self._handle_message_receive
        self._event_handlers[LarkEventType.MESSAGE_READ] = self._handle_message_read

        # 启动 WebSocket 长连接
        self._running = True
        self._should_reconnect = True
        self._ws_error_count = 0
        self._ws_task = asyncio.create_task(self._ws_loop(), name="lark_ws")
        logger.info("Lark adapter started (WebSocket mode)")

    async def stop(self) -> None:
        """停止适配器：断开 WebSocket + 清理资源"""
        self._running = False
        self._should_reconnect = False

        # 取消 WebSocket 任务
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Lark WS task cleanup: %s", e)

        # 停止 WebSocket 客户端
        if self._ws_client:
            try:
                await self._ws_client.stop()
            except Exception as e:
                logger.debug("Lark WS client stop: %s", e)
            self._ws_client = None

        self._client = None
        self._session_users.clear()
        logger.info("Lark adapter stopped")

    # ==================================================================
    # ChannelAdapter 接口 — send_response
    # ==================================================================

    async def send_response(self, response: ChannelResponse, original: ChannelMessage) -> None:
        """
        向飞书发送响应消息

        策略（参考 Hermes + openclaw-lark）:
        - 优先级1: 作为回复消息（reply_to message_id）
        - 优先级2: 发送到群聊/私聊 session
        - 自动选择: 纯文本用 text 类型，格式文本用 post 类型
        """
        chat_id = self._resolve_send_target(original)
        if not chat_id:
            logger.error("Lark send_response: 无法解析 chat_id")
            return

        msg_type = response.metadata.get("msg_type", "text")
        reply_msg_id = original.metadata.get("msg_id") or original.message_id

        try:
            if msg_type == "post" or self._has_markdown_formatting(response.content):
                content_json = self._build_post_content(response.content)
            else:
                content_json = json.dumps({"text": response.content})

            await self._send_message(
                chat_id=chat_id,
                content=content_json,
                msg_type="post" if "title" in content_json else "text",
                reply_msg_id=reply_msg_id,
            )
        except Exception as e:
            logger.exception("Lark send_response failed: chat_id=%s", chat_id)

    # ==================================================================
    # ChannelAdapter 接口 — get_user_info
    # ==================================================================

    async def get_user_info(self, user_id: str) -> ChannelUser | None:
        """通过 lark-oapi Contact API 获取用户信息"""
        if not self._client or not self._running:
            return None

        try:
            request = ListUserRequest.builder() \
                .user_id_type("open_id") \
                .user_id(user_id) \
                .build()

            response = await asyncio.to_thread(
                self._client.contact.v3.user.list, request
            )

            if response.code != 0 or not response.data:
                logger.warning("Lark get_user_info: %s code=%s", user_id, response.code)
                return ChannelUser(
                    user_id=user_id,
                    display_name=f"Feishu User ({user_id[:12]})",
                )

            items = response.data.items
            if items and len(items) > 0:
                user = items[0]
                return ChannelUser(
                    user_id=user_id,
                    display_name=user.name or f"Feishu User ({user_id[:12]})",
                    metadata={
                        "open_id": user.open_id,
                        "union_id": user.union_id,
                        "email": user.email,
                        "mobile": user.mobile,
                        "avatar_url": user.avatar_url,
                    },
                )

        except Exception as e:
            logger.exception("Lark get_user_info error: user_id=%s", user_id)

        return ChannelUser(
            user_id=user_id,
            display_name=f"Feishu User ({user_id[:12]})",
        )

    # ==================================================================
    # WebSocket 连接管理 (Layer 1)
    # ==================================================================

    async def _ws_loop(self) -> None:
        """WebSocket 长连接循环（参考 openclaw-lark monitor 模式）"""
        while self._running and self._should_reconnect:
            try:
                domain = (
                    lark.FEISHU_DOMAIN
                    if self._lark_config.domain == "feishu"
                    else lark.LARK_DOMAIN
                )
                self._ws_client = lark.ws.Client(
                    app_id=self._lark_config.app_id,
                    app_secret=self._lark_config.app_secret,
                    event_handler=self._dispatch_event,
                    domain=domain,
                    auto_reconnect=self._lark_config.auto_reconnect,
                )
                logger.info("Lark WebSocket connecting...")
                await self._ws_client.start()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws_error_count += 1
                logger.warning(
                    "Lark WebSocket error #%d, reconnect in 5s: %s",
                    self._ws_error_count, e,
                )
                if self._ws_error_count >= self._max_ws_errors:
                    logger.error("Lark WebSocket: too many errors, stopping")
                    self._running = False
                    break
                await asyncio.sleep(5)

    # ==================================================================
    # 事件分发 (Layer 1)
    # ==================================================================

    async def _dispatch_event(self, event: Any) -> None:
        """事件分发（参考 openclaw-lark 回调模式）"""
        event_type = getattr(event, "type", "") or str(event)
        handler = self._event_handlers.get(event_type)
        if handler:
            try:
                await handler(event)
            except Exception as e:
                logger.exception("Lark event handler error [%s]: %s", event_type, e)

    # ==================================================================
    # 消息接收处理 (Layer 2: Hermes vendor)
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
            root_id = getattr(message, "root_id", "")
            parent_id = getattr(message, "parent_id", "")

            content = self._extract_text_content(message, msg_type)
            if not content:
                return

            # 使用 Hermes vendor 提取 @提及
            mentions = self._extract_mentions(message)

            channel_msg = ChannelMessage(
                message_id=msg_id,
                channel_type=ChannelType.FEISHU,
                user=ChannelUser(user_id=open_id),
                content=content,
                session_id=chat_id,
                reply_to=root_id or parent_id or None,
                metadata={
                    "chat_id": chat_id,
                    "msg_type": msg_type,
                    "open_id": open_id,
                    "root_id": root_id,
                    "parent_id": parent_id,
                    "mentions": mentions,
                },
            )
            self._session_users[chat_id] = open_id
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
                logger.debug(
                    "Lark 消息已读: reader=%s, count=%d",
                    getattr(reader, "open_id", "?"),
                    len(msg_id_list or []),
                )
        except Exception:
            pass

    # ==================================================================
    # API 调用 — 发送消息 (Layer 1: lark-oapi)
    # ==================================================================

    async def _send_message(
        self,
        chat_id: str,
        content: str,
        msg_type: str = "text",
        reply_msg_id: str | None = None,
        retries: int = 0,
    ) -> str:
        """通过 lark-oapi CreateMessage API 发送消息"""
        if not self._client:
            raise RuntimeError("Lark client not initialized")

        try:
            body = CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type(msg_type) \
                .content(content) \
                .build()

            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(body) \
                .build()

            response = await asyncio.to_thread(
                self._client.im.v1.message.create, request
            )

            if response.code != 0:
                error_msg = f"code={response.code} msg={response.msg}"
                logger.error("Lark send_message failed: %s", error_msg)
                if retries < self._lark_config.max_retries:
                    delay = self._lark_config.retry_delay * (2 ** retries)
                    await asyncio.sleep(delay)
                    return await self._send_message(
                        chat_id, content, msg_type, reply_msg_id, retries + 1
                    )
                return ""

            msg_id = ""
            if response.data:
                msg_id = getattr(response.data, "message_id", "")
            logger.debug("Lark message sent: msg_id=%s chat_id=%s", msg_id, chat_id)
            return msg_id

        except Exception as e:
            logger.exception("Lark send_message exception: chat_id=%s", chat_id)
            if retries < self._lark_config.max_retries:
                delay = self._lark_config.retry_delay * (2 ** retries)
                await asyncio.sleep(delay)
                return await self._send_message(
                    chat_id, content, msg_type, reply_msg_id, retries + 1
                )
            raise

    # ==================================================================
    # API 调用 — 媒体消息
    # ==================================================================

    async def send_image(
        self, chat_id: str, image_data: bytes, image_type: str = "message"
    ) -> str:
        """发送图片消息（对齐 Hermes send_image / openclaw-lark media）"""
        if not self._client:
            raise RuntimeError("Lark client not initialized")

        try:
            body = CreateImageRequestBody.builder() \
                .image_type(image_type) \
                .image(image_data) \
                .build()
            request = CreateImageRequest.builder() \
                .request_body(body) \
                .build()

            response = await asyncio.to_thread(
                self._client.im.v1.image.create, request
            )
            if response.code != 0 or not response.data:
                logger.error("Lark upload_image failed: code=%s", response.code)
                return ""

            image_key = response.data.image_key
            content = json.dumps({"image_key": image_key})
            return await self._send_message(chat_id, content, msg_type="image")

        except Exception as e:
            logger.exception("Lark send_image failed")
            raise

    async def send_file(
        self, chat_id: str, file_data: bytes, file_name: str, file_type: str = "stream"
    ) -> str:
        """发送文件消息（对齐 Hermes send_document / openclaw-lark media）"""
        if not self._client:
            raise RuntimeError("Lark client not initialized")

        try:
            body = CreateFileRequestBody.builder() \
                .file_type(file_type) \
                .file_name(file_name) \
                .file(file_data) \
                .build()
            request = CreateFileRequest.builder() \
                .request_body(body) \
                .build()

            response = await asyncio.to_thread(
                self._client.im.v1.file.create, request
            )
            if response.code != 0 or not response.data:
                logger.error("Lark upload_file failed: code=%s", response.code)
                return ""

            file_key = response.data.file_key
            content = json.dumps({"file_key": file_key})
            return await self._send_message(chat_id, content, msg_type="file")

        except Exception as e:
            logger.exception("Lark send_file failed")
            raise

    # ==================================================================
    # 工具方法 — 消息内容解析 (Layer 2: Hermes vendor)
    # ==================================================================

    @staticmethod
    def _extract_text_content(message: Any, msg_type: str) -> str:
        """
        从飞书消息体中提取文本内容

        支持类型:
          - text: 纯文本消息
          - post: 富文本消息（使用 Hermes vendor parse_feishu_post_payload）
          - interactive: 互动卡片消息
        """
        raw = getattr(message, "content", "")
        if isinstance(raw, str):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return raw
        elif isinstance(raw, dict):
            body = raw
        else:
            return str(raw) if raw else ""

        if msg_type == "text":
            return body.get("text", "")

        if msg_type == "post":
            try:
                # 使用 Hermes vendor 解析富文本
                content_parts = parse_feishu_post_payload(body)
                return " ".join(content_parts)
            except Exception:
                # Fallback: 简单提取
                return str(body)[:500]

        if msg_type == "interactive":
            try:
                card = body.get("card", {})
                elements = card.get("elements", [])
                texts = []
                for elem in elements:
                    tag = elem.get("tag", "")
                    if tag == "div":
                        text_elem = elem.get("text", {})
                        texts.append(text_elem.get("content", ""))
                    elif tag == "markdown":
                        texts.append(elem.get("content", ""))
                return " ".join(texts)
            except Exception:
                return str(body)[:500]

        if msg_type == "image":
            image_key = body.get("image_key", "")
            return f"[Image: {image_key}]"

        if msg_type == "file":
            file_key = body.get("file_key", "")
            file_name = body.get("file_name", "unknown")
            return f"[File: {file_name} (key={file_key})]"

        if msg_type == "audio":
            file_key = body.get("file_key", "")
            return f"[Audio: key={file_key}]"

        if msg_type == "media":
            file_key = body.get("file_key", "")
            return f"[Media: key={file_key}]"

        return ""

    @staticmethod
    def _extract_mentions(message: Any) -> list[str]:
        """使用 Hermes vendor 提取 @提及的用户列表"""
        raw = getattr(message, "content", "")
        if isinstance(raw, str):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return []
        else:
            body = raw

        if not isinstance(body, dict):
            return []

        # 使用 Hermes vendor 的 mention 提取逻辑
        mentions = body.get("mentions", [])
        result = []
        for m in mentions:
            if isinstance(m, dict):
                result.append(m.get("key", "") or m.get("name", ""))
        return result

    # ==================================================================
    # 工具方法 — 消息格式转换 (Layer 2: Hermes vendor)
    # ==================================================================

    @staticmethod
    def _has_markdown_formatting(text: str) -> bool:
        """检测文本是否包含 Markdown 格式"""
        markers = ["**", "__", "*", "`", "```", "#", "- ", "1. ", "> ", "![", "[", "|"]
        return any(m in text for m in markers)

    def _build_post_content(self, text: str) -> str:
        """
        将文本转换为飞书 Post 格式的 JSON

        使用 Hermes vendor 的 _build_markdown_post_payload
        """
        try:
            return _build_markdown_post_payload(text)
        except Exception:
            # Fallback: 简单段落拆分
            paragraphs = text.split("\n\n")
            content_list = []
            for para in paragraphs:
                if para.strip():
                    p = [{"tag": "text", "text": para.strip()}]
                else:
                    p = [{"tag": "text", "text": ""}]
                content_list.append(p)
            payload = {
                "zh_cn": {"title": "", "content": content_list},
            }
            return json.dumps(payload, ensure_ascii=False)

    # ==================================================================
    # 工具方法 — 目标解析
    # ==================================================================

    def _resolve_send_target(self, message: ChannelMessage) -> str:
        """解析发送目标 chat_id"""
        chat_id = message.metadata.get("chat_id", "")
        if not chat_id:
            chat_id = message.session_id
        return chat_id

    # ==================================================================
    # HTTP Webhook 兼容
    # ==================================================================

    async def handle_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        """处理 HTTP Webhook 回调（URL 验证 + 事件解密）"""
        if body.get("type") == LarkEventType.URL_VERIFICATION:
            return {"challenge": body.get("challenge", "")}
        return {}


# ============================================================================
# 工厂函数
# ============================================================================

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
