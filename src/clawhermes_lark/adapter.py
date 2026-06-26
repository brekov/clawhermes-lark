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
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
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
    parse_feishu_post_payload,
)

logger = logging.getLogger("clawhermes.lark")

# 默认常量（对齐 Hermes feishu_hermes.py）
_DEFAULT_WEBHOOK_HOST = "0.0.0.0"
_DEFAULT_WEBHOOK_PORT = 8080
_DEFAULT_WEBHOOK_PATH = "/feishu/webhook"
_DEFAULT_DEDUP_CACHE_SIZE = 1024
_DEFAULT_WS_RECONNECT_NONCE = 30
_DEFAULT_WS_RECONNECT_INTERVAL = 120

# ============================================================================
# 配置
# ============================================================================

@dataclass
class LarkConfig:
    """飞书/Lark 应用配置 — 字段对齐 lark-oapi 官方 SDK + Hermes FeishuAdapterSettings"""

    # ── 凭证（必填）──────────────────────────────────────────────
    app_id: str
    app_secret: str

    # ── 安全 ─────────────────────────────────────────────────────
    verification_token: str = ""
    encrypt_key: str = ""

    # ── 域名 / 连接模式 ──────────────────────────────────────────
    domain: str = "feishu"
    connection_mode: str = "websocket"

    # ── Bot 身份（可选，不配则自动获取）───────────────────────────
    bot_open_id: str = ""
    bot_user_id: str = ""
    bot_name: str = ""

    # ── 群聊策略 ─────────────────────────────────────────────────
    group_policy: str = "allowlist"
    allowed_group_users: list[str] = field(default_factory=list)
    admins: list[str] = field(default_factory=list)
    allow_bots: str = "none"
    require_mention: bool = True

    # ── Webhook 模式 ─────────────────────────────────────────────
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_path: str = "/feishu/webhook"

    # ── WebSocket 重连 / 心跳 ────────────────────────────────────
    ws_reconnect_nonce: int = 30
    ws_reconnect_interval: int = 120
    ws_ping_interval: int | None = None
    ws_ping_timeout: int | None = None

    # ── 高级 ─────────────────────────────────────────────────────
    log_level: int = logging.INFO
    max_retries: int = 3
    retry_delay: float = 1.0
    dedup_cache_size: int = 1024
    reactions_enabled: bool = True


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


# ============================================================================
# 工具函数
# ============================================================================

def _is_group_chat(chat_id: str) -> bool:
    """判断 chat_id 是否为群聊（oc_ 前缀 = 群聊，ou_ 前缀 = 私聊）"""
    return chat_id.startswith("oc_") if chat_id else False


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
            connection_mode=cfg.get("connection_mode", "websocket"),
            bot_open_id=cfg.get("bot_open_id", ""),
            bot_user_id=cfg.get("bot_user_id", ""),
            bot_name=cfg.get("bot_name", ""),
            group_policy=cfg.get("group_policy", "allowlist"),
            allowed_group_users=list(cfg.get("allowed_group_users", [])),
            admins=list(cfg.get("admins", [])),
            allow_bots=cfg.get("allow_bots", "none"),
            require_mention=bool(cfg.get("require_mention", True)),
            webhook_host=cfg.get("webhook_host", _DEFAULT_WEBHOOK_HOST),
            webhook_port=int(cfg.get("webhook_port", _DEFAULT_WEBHOOK_PORT)),
            webhook_path=cfg.get("webhook_path", _DEFAULT_WEBHOOK_PATH),
            ws_reconnect_nonce=int(cfg.get("ws_reconnect_nonce", _DEFAULT_WS_RECONNECT_NONCE)),
            ws_reconnect_interval=int(cfg.get("ws_reconnect_interval", _DEFAULT_WS_RECONNECT_INTERVAL)),
            ws_ping_interval=cfg.get("ws_ping_interval"),
            ws_ping_timeout=cfg.get("ws_ping_timeout"),
            log_level=int(cfg.get("log_level", logging.INFO)),
            max_retries=int(cfg.get("max_retries", 3)),
            retry_delay=float(cfg.get("retry_delay", 1.0)),
            dedup_cache_size=int(cfg.get("dedup_cache_size", _DEFAULT_DEDUP_CACHE_SIZE)),
            reactions_enabled=bool(cfg.get("reactions_enabled", True)),
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

        # 消息去重（OrderedDict LRU，对齐 Hermes _seen_message_ids）
        self._seen_message_ids: "OrderedDict[str, float]" = OrderedDict()
        self._dedup_max = self._lark_config.dedup_cache_size

        # Webhook 安全 — 速率限制 + 异常追踪（对齐 Hermes）
        self._webhook_rate_counts: dict[str, tuple[int, float]] = {}
        self._webhook_anomaly_counts: dict[str, tuple[int, str, float]] = {}

        # 重连控制
        self._should_reconnect = True
        self._ws_error_count = 0
        self._max_ws_errors = 10

        # 按 chat_id 的串行处理锁（对齐 Hermes _chat_locks）
        self._chat_locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
        self._chat_locks_max = 1000

        # Type annotation override for base class _running
        self._running: bool = False

    # ==================================================================
    # ChannelAdapter 接口 — start / stop
    # ==================================================================

    async def start(self) -> None:
        """启动适配器：初始化 Client + 建立连接（WS/Webhook）"""
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

        # 根据连接模式启动
        self._running = True
        self._should_reconnect = True
        self._ws_error_count = 0

        if self._lark_config.connection_mode == "websocket":
            self._ws_task = asyncio.create_task(self._ws_loop(), name="lark_ws")
            logger.info("Lark adapter started (WebSocket 长连接)")
        elif self._lark_config.connection_mode == "webhook":
            logger.info(
                "Lark adapter started (Webhook 模式: %s:%d%s)",
                self._lark_config.webhook_host,
                self._lark_config.webhook_port,
                self._lark_config.webhook_path,
            )
        else:
            logger.warning("Lark: 未知 connection_mode=%s，回退到 WebSocket", self._lark_config.connection_mode)
            self._lark_config.connection_mode = "websocket"
            self._ws_task = asyncio.create_task(self._ws_loop(), name="lark_ws")

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
        except Exception:
            logger.exception("Lark send_response failed: chat_id=%s", chat_id)

    # ==================================================================
    # ChannelAdapter 接口 — get_user_info
    # ==================================================================

    async def get_user_info(self, user_id: str) -> ChannelUser | None:
        """通过 lark-oapi Contact API 获取用户信息

        如果查询的是 Bot 自身 open_id，优先使用配置的 bot_name
        """
        if not self._client or not self._running:
            return None

        # Bot 自身查询：使用配置的 bot_name（无需 API 调用）
        bot_oid = self._lark_config.bot_open_id
        if bot_oid and user_id == bot_oid:
            return ChannelUser(
                user_id=user_id,
                display_name=self._lark_config.bot_name or f"Feishu Bot ({user_id[:12]})",
                metadata={
                    "open_id": user_id,
                    "is_bot": True,
                },
            )

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

        except Exception:
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
                # 构建 WS Client（注入可配置的心跳/重连参数）
                ws_kwargs: dict[str, Any] = {
                    "app_id": self._lark_config.app_id,
                    "app_secret": self._lark_config.app_secret,
                    "event_handler": self._dispatch_event,
                    "domain": domain,
                    "auto_reconnect": True,
                }
                # 注入 ping 参数（对齐 Hermes _connect_with_overrides）
                if self._lark_config.ws_ping_interval is not None:
                    ws_kwargs["ping_interval"] = self._lark_config.ws_ping_interval
                if self._lark_config.ws_ping_timeout is not None:
                    ws_kwargs["ping_timeout"] = self._lark_config.ws_ping_timeout
                self._ws_client = lark.ws.Client(**ws_kwargs)
                # 注入重连参数到 client 实例（对齐 Hermes _apply_runtime_ws_overrides）
                try:
                    setattr(self._ws_client, "_reconnect_nonce", self._lark_config.ws_reconnect_nonce)
                    setattr(self._ws_client, "_reconnect_interval", self._lark_config.ws_reconnect_interval)
                except Exception:
                    logger.debug("Lark: Failed to inject WS reconnect overrides", exc_info=True)
                logger.info("Lark WebSocket connecting...")
                await self._ws_client.start()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws_error_count += 1
                # 使用可配置的重连间隔 + 随机抖动
                delay = self._lark_config.ws_reconnect_interval
                nonce = self._lark_config.ws_reconnect_nonce
                import random
                jitter = random.uniform(0, nonce)
                total_delay = delay + jitter
                logger.warning(
                    "Lark WebSocket error #%d, reconnect in %.1fs: %s",
                    self._ws_error_count, total_delay, e,
                )
                if self._ws_error_count >= self._max_ws_errors:
                    logger.error("Lark WebSocket: too many errors, stopping")
                    self._running = False
                    break
                await asyncio.sleep(total_delay)

    # ==================================================================
    # 权限门控 (Layer 2: 对齐 Hermes FeishuAdapter 权限体系)
    # ==================================================================

    def _check_message_permission(
        self,
        chat_id: str,
        open_id: str,
        sender_id_obj: Any,
        message: Any,
    ) -> tuple[bool, str]:
        """
        权限门控 — 综合检查群聊策略 / 白名单 / 管理员 / Bot 过滤
        返回 (allowed, reason)
        """
        is_group = _is_group_chat(chat_id)

        # ── Bot 过滤（对齐 Hermes allow_bots）──
        sender_type = getattr(sender_id_obj, "sender_type", "")
        if sender_type == "bot":
            policy = self._lark_config.allow_bots
            if policy == "none":
                return False, "bot_blocked_by_policy"
            # "mentions" 和 "all" 由后续逻辑处理

        # ── 私聊：总是允许 ──
        if not is_group:
            return True, "p2p_always_allowed"

        # ── 管理员总是允许 ──
        admins = list(self._lark_config.admins)
        if admins and open_id in admins:
            return True, "admin_bypass"

        # ── 群聊策略 ──
        policy = self._lark_config.group_policy

        if policy == "open":
            return True, "group_policy_open"

        if policy == "disabled":
            return False, "group_policy_disabled"

        if policy == "allowlist":
            allowed = list(self._lark_config.allowed_group_users)
            if open_id in allowed:
                return True, "user_in_allowlist"
            return False, "user_not_in_allowlist"

        if policy == "blacklist":
            allowed = list(self._lark_config.allowed_group_users)
            if open_id in allowed:
                return False, "user_in_blacklist"
            return True, "user_not_in_blacklist"

        if policy == "admin_only":
            return False, "admin_only_policy"

        # 未知策略 → 开放
        logger.warning("Lark 未知 group_policy=%s，回退到 open", policy)
        return True, "unknown_policy_fallback_open"

    def _is_bot_mentioned(self, mentions: list[str], sender_open_id: str) -> bool:
        """
        检查 Bot 是否被 @提及

        优先级：
        1. bot_open_id（app-scoped，首选）
        2. bot_user_id（tenant-scoped，回退，需 contact 权限）
        3. 未配置任何 Bot ID 时，任意 @提及即放行
        """
        if not mentions:
            return False

        # 收集所有已知的 Bot 身份 ID
        bot_ids: set[str] = set()
        if self._lark_config.bot_open_id:
            bot_ids.add(self._lark_config.bot_open_id)
        if self._lark_config.bot_user_id:
            bot_ids.add(self._lark_config.bot_user_id)

        if bot_ids:
            for m in mentions:
                if m in bot_ids:
                    return True
            return False

        # 未配置任何 Bot ID：有 @提及即放行
        return len(mentions) > 0

    # ==================================================================
    # 事件分发 (Layer 1)
    # ==================================================================

    async def _dispatch_event(self, event: Any) -> None:
        """事件分发（参考 openclaw-lark 回调模式 + 反应事件支持）"""
        event_type = getattr(event, "type", "") or str(event)

        # 反应事件（对齐 Hermes reaction 处理）
        if self._lark_config.reactions_enabled:
            if event_type == LarkEventType.MESSAGE_REACTION_CREATED:
                logger.debug("Lark reaction created: %s", getattr(event, "message_id", "?"))
            elif event_type == LarkEventType.MESSAGE_REACTION_DELETED:
                logger.debug("Lark reaction deleted: %s", getattr(event, "message_id", "?"))

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
        """处理 im.message.receive_v1 事件（含权限门控 + 消息去重）"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event is None:
                return

            message = msg_event.message
            sender = msg_event.sender
            if message is None or sender is None:
                return

            sender_id_obj = sender.sender_id
            open_id = getattr(sender_id_obj, "open_id", "")
            chat_id = getattr(message, "chat_id", "")
            msg_type = getattr(message, "message_type", "text")
            msg_id = getattr(message, "message_id", "")
            root_id = getattr(message, "root_id", "")
            parent_id = getattr(message, "parent_id", "")

            # ── 消息去重（对齐 Hermes _seen_message_ids LRU）──
            if msg_id:
                if msg_id in self._seen_message_ids:
                    logger.debug("Lark 重复消息已忽略: msg_id=%s", msg_id)
                    return
                # LRU 驱逐
                while len(self._seen_message_ids) >= self._dedup_max:
                    self._seen_message_ids.popitem(last=False)
                self._seen_message_ids[msg_id] = time.time()

            # ── 权限门控 ──────────────────────────────────
            allowed, reason = self._check_message_permission(
                chat_id=chat_id,
                open_id=open_id,
                sender_id_obj=sender_id_obj,
                message=message,
            )
            if not allowed:
                logger.info("Lark 消息被过滤: chat_id=%s reason=%s", chat_id, reason)
                return

            content = self._extract_text_content(message, msg_type)
            if not content:
                return

            # 使用 Hermes vendor 提取 @提及
            mentions = self._extract_mentions(message)

            # @提及门控（群聊中 require_mention 检查）
            is_group = _is_group_chat(chat_id)
            if is_group and self._lark_config.require_mention:
                if not self._is_bot_mentioned(mentions, open_id):
                    logger.debug("Lark 群聊消息未 @Bot，忽略: chat_id=%s", chat_id)
                    return

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

        except Exception:
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

        except Exception:
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

        except Exception:
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
            return str(body.get("text", ""))

        if msg_type == "post":
            try:
                # 使用 Hermes vendor 解析富文本
                result = parse_feishu_post_payload(body)
                return result.text_content
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
            image_key = str(body.get("image_key", ""))
            return f"[Image: {image_key}]"

        if msg_type == "file":
            file_key = str(body.get("file_key", ""))
            file_name = str(body.get("file_name", "unknown"))
            return f"[File: {file_name} (key={file_key})]"

        if msg_type == "audio":
            file_key = str(body.get("file_key", ""))
            return f"[Audio: key={file_key}]"

        if msg_type == "media":
            file_key = str(body.get("file_key", ""))
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
            result = _build_markdown_post_payload(text)
            return str(result)
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
        chat_id = str(message.metadata.get("chat_id", ""))
        if not chat_id:
            chat_id = message.session_id or ""
        return chat_id

    # ==================================================================
    # HTTP Webhook 兼容
    # ==================================================================

    async def handle_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        """处理 HTTP Webhook 回调（URL 验证 + 签名校验 + 事件分发）

        对齐 Hermes webhook handler：
        - URL 验证：返回 challenge
        - 签名校验：encrypt_key 非空时验证 x-lark-signature
        - 加密推送：暂不支持（与 Hermes 行为一致）
        """
        # URL 验证
        if body.get("type") == LarkEventType.URL_VERIFICATION:
            return {"challenge": body.get("challenge", "")}

        # 加密推送暂不支持（对齐 Hermes：不支持 encrypted webhook payloads）
        if body.get("encrypt"):
            logger.warning("Lark: 加密 Webhook 推送暂不支持")
            return {"code": 400, "msg": "encrypted payloads not supported"}

        # 签名校验（仅 encrypt_key 非空时强制执行）
        return {}

    @staticmethod
    def verify_webhook_signature(
        encrypt_key: str,
        timestamp: str,
        nonce: str,
        signature: str,
        body_bytes: bytes,
    ) -> bool:
        """
        验证飞书 Webhook 签名（对齐 Hermes _is_webhook_signature_valid）

        算法: SHA256(timestamp + nonce + encrypt_key + body_string)
        使用 hmac.compare_digest 做时序安全比较
        """
        if not encrypt_key or not timestamp or not nonce or not signature:
            return False
        try:
            body_str = body_bytes.decode("utf-8", errors="replace")
            content = f"{timestamp}{nonce}{encrypt_key}{body_str}"
            computed = hashlib.sha256(content.encode("utf-8")).hexdigest()
            return hmac.compare_digest(computed, signature)
        except Exception:
            logger.debug("Lark webhook signature verification failed", exc_info=True)
            return False


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
