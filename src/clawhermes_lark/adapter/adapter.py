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
  - 消息去重 (FIFO) + 按 chat 串行队列
  - 中止快速通道 (abort fast-path)
  - 反应事件路由（Reaction as synthetic event）
  - 交互式卡片回调 (card.action.trigger)
  - 多账户支持
  - Hermes 生产级消息引擎复用
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
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

# Hermes vendor — 消息解析/格式化引擎
from clawhermes_lark.hermes_vendor import (
    _build_markdown_post_payload,
    parse_feishu_post_payload,
)

# ── ClawHermes-Lark 增强模块 ────────────────────────────────────────────
from clawhermes_lark.openclaw.chat_queue import (
    ActiveDispatcherEntry,
    build_queue_key,
    enqueue_feishu_chat_task,
    get_active_dispatcher,
    has_active_task,
    register_active_dispatcher,
    unregister_active_dispatcher,
)
from clawhermes_lark.openclaw.dedup import MessageDedup, is_message_expired
from clawhermes_lark.openclaw.abort_detect import (
    extract_raw_text_from_event,
    is_likely_abort_text,
)
from clawhermes_lark.openclaw.targets import (
    normalize_feishu_target,
    resolve_receive_id_type,
)
from clawhermes_lark.openclaw.interactive import get_interactive_dispatcher
from clawhermes_lark.openclaw.accounts import (
    DEFAULT_ACCOUNT_ID,
    LarkAccount,
    get_lark_account,
)

logger = logging.getLogger("clawhermes.lark")

# 默认常量（对齐 Hermes feishu_hermes.py）
_DEFAULT_WEBHOOK_HOST = "0.0.0.0"
_DEFAULT_WEBHOOK_PORT = 8080
_DEFAULT_WEBHOOK_PATH = "/feishu/webhook"
_DEFAULT_DEDUP_CACHE_SIZE = 2048
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

    # ── 域名 / 品牌 / 连接模式 ─────────────────────────────────
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

    # ── Reaction 通知设置 ───────────────────────────────────────
    reactions_enabled: bool = True
    reaction_notifications: str = "own"  # "off" | "own" | "all"

    # ── Webhook 模式 ─────────────────────────────────────────────
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_path: str = "/feishu/webhook"

    # ── WebSocket 重连 / 心跳 ────────────────────────────────────
    ws_reconnect_nonce: int = 30
    ws_reconnect_interval: int = 120
    ws_ping_interval: int | None = None
    ws_ping_timeout: int | None = None

    # ── 账户标识 ─────────────────────────────────────────────────
    account_id: str = "default"

    # ── 高级 ─────────────────────────────────────────────────────
    log_level: int = logging.INFO
    max_retries: int = 3
    retry_delay: float = 1.0
    dedup_cache_size: int = 2048


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
    BOT_ADDED_TO_CHAT = "im.chat.member.bot.added_v1"
    BOT_REMOVED_FROM_CHAT = "im.chat.member.bot.deleted_v1"
    CARD_ACTION_TRIGGER = "card.action.trigger"
    URL_VERIFICATION = "url_verification"
    VC_MEETING_INVITED = "vc.bot.meeting_invited_v1"
    DRIVE_COMMENT_ADD = "drive.notice.comment_add_v1"


# ============================================================================
# 工具函数
# ============================================================================

def _is_group_chat(chat_id: str) -> bool:
    """判断 chat_id 是否为群聊（oc_ 前缀 = 群聊，ou_ 前缀 = 私聊）"""
    return chat_id.startswith("oc_") if chat_id else False


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
      - 消息分发: on_message -> _dispatch_message
    """

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(ChannelType.FEISHU, config)
        cfg = config or {}

        self._lark_config = LarkConfig(
            app_id=cfg.get("app_id") or cfg.get("appId", ""),
            app_secret=cfg.get("app_secret") or cfg.get("appSecret", ""),
            verification_token=cfg.get("verification_token") or cfg.get("verificationToken", ""),
            encrypt_key=cfg.get("encrypt_key") or cfg.get("encryptKey", ""),
            domain=cfg.get("domain") or cfg.get("brand", "feishu"),
            connection_mode=cfg.get("connection_mode") or cfg.get("connectionMode", "websocket"),
            bot_open_id=cfg.get("bot_open_id") or cfg.get("botOpenId", ""),
            bot_user_id=cfg.get("bot_user_id") or cfg.get("botUserId", ""),
            bot_name=cfg.get("bot_name") or cfg.get("botName", ""),
            group_policy=cfg.get("group_policy") or cfg.get("groupPolicy", "allowlist"),
            allowed_group_users=list(cfg.get("allowed_group_users", [])),
            admins=list(cfg.get("admins", [])),
            allow_bots=cfg.get("allow_bots") or cfg.get("allowBots", "none"),
            require_mention=bool(cfg.get("require_mention", True)),
            webhook_host=cfg.get("webhook_host") or cfg.get("webhookHost", _DEFAULT_WEBHOOK_HOST),
            webhook_port=int(cfg.get("webhook_port") or cfg.get("webhookPort", _DEFAULT_WEBHOOK_PORT)),
            webhook_path=cfg.get("webhook_path") or cfg.get("webhookPath", _DEFAULT_WEBHOOK_PATH),
            ws_reconnect_nonce=int(cfg.get("ws_reconnect_nonce", _DEFAULT_WS_RECONNECT_NONCE)),
            ws_reconnect_interval=int(cfg.get("ws_reconnect_interval", _DEFAULT_WS_RECONNECT_INTERVAL)),
            ws_ping_interval=cfg.get("ws_ping_interval"),
            ws_ping_timeout=cfg.get("ws_ping_timeout"),
            log_level=int(cfg.get("log_level", logging.INFO)),
            max_retries=int(cfg.get("max_retries", 3)),
            retry_delay=float(cfg.get("retry_delay", 1.0)),
            dedup_cache_size=int(cfg.get("dedup_cache_size", _DEFAULT_DEDUP_CACHE_SIZE)),
            reactions_enabled=bool(cfg.get("reactions_enabled", True)),
            reaction_notifications=cfg.get("reaction_notifications") or cfg.get("reactionNotifications", "own"),
            account_id=cfg.get("account_id") or cfg.get("accountId", "default"),
        )

        # Layer 1: lark-oapi Client（Token 自动管理）
        self._client: lark.Client | None = None

        # Layer 1: WebSocket 客户端
        self._ws_client: lark.ws.Client | None = None
        self._ws_task: asyncio.Task | None = None

        # 事件处理器注册表
        self._event_handlers: dict[str, Callable] = {}

        # ── 增强：使用 MessageDedup 替代简单的 OrderedDict ──
        self._message_dedup = MessageDedup(
            ttl_ms=12 * 60 * 60 * 1000,  # 12h TTL
            max_entries=self._lark_config.dedup_cache_size,
        )

        # open_id 缓存 (chat_id -> open_id)
        self._session_users: dict[str, str] = {}

        # Webhook 安全 — 速率限制 + 异常追踪
        self._webhook_rate_counts: dict[str, tuple[int, float]] = {}
        self._webhook_anomaly_counts: dict[str, tuple[int, str, float]] = {}

        # 重连控制
        self._should_reconnect = True
        self._ws_error_count = 0
        self._max_ws_errors = 10

        # ── 反应事件支持（bots own message tracking）──
        self._bot_message_ids: "OrderedDict[str, float]" = OrderedDict()
        self._bot_msg_max = 500

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
            "Lark client initialized: app_id=%s domain=%s account=%s",
            self._lark_config.app_id[:8] + "***",
            self._lark_config.domain,
            self._lark_config.account_id,
        )

        # 注册核心事件处理器
        self._event_handlers[LarkEventType.MESSAGE_RECEIVE] = self._handle_message_receive
        self._event_handlers[LarkEventType.MESSAGE_READ] = self._handle_message_read

        # ── 增强：注册 Reaction 事件处理器 ──
        if self._lark_config.reactions_enabled:
            self._event_handlers[LarkEventType.MESSAGE_REACTION_CREATED] = (
                self._handle_reaction_created
            )
            self._event_handlers[LarkEventType.MESSAGE_REACTION_DELETED] = (
                self._handle_reaction_deleted
            )

        # ── 增强：注册 Bot 群聊成员事件 ──
        self._event_handlers[LarkEventType.BOT_ADDED_TO_CHAT] = (
            self._handle_bot_added_to_chat
        )
        self._event_handlers[LarkEventType.BOT_REMOVED_FROM_CHAT] = (
            self._handle_bot_removed_from_chat
        )

        # ── 增强：注册 VC Meeting / Drive Comment 事件 ──
        self._event_handlers[LarkEventType.VC_MEETING_INVITED] = (
            self._handle_vc_meeting_invited
        )
        self._event_handlers[LarkEventType.DRIVE_COMMENT_ADD] = (
            self._handle_drive_comment_add
        )

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

        # 清理增强模块
        self._message_dedup.dispose()

        self._client = None
        self._session_users.clear()
        logger.info("Lark adapter stopped")

    # ==================================================================
    # ChannelAdapter 接口 — send_response
    # ==================================================================

    async def send_response(self, response: ChannelResponse, original: ChannelMessage) -> None:
        """向飞书发送响应消息"""
        chat_id = self._resolve_send_target(original)
        if not chat_id:
            logger.error("Lark send_response: 无法解析 chat_id")
            return

        msg_type = response.metadata.get("msg_type", "text")

        try:
            if msg_type == "interactive" or msg_type == "card":
                # Card message
                card = response.metadata.get("card", {})
                if card:
                    await self._send_card_message(
                        chat_id=chat_id,
                        card=card,
                        reply_msg_id=original.metadata.get("msg_id") or original.message_id,
                    )
            elif msg_type == "post" or self._has_markdown_formatting(response.content):
                content_json = self._build_post_content(response.content)
                await self._send_message(
                    chat_id=chat_id,
                    content=content_json,
                    msg_type="post",
                    reply_msg_id=original.metadata.get("msg_id") or original.message_id,
                )
            else:
                content_json = json.dumps({"text": response.content})
                await self._send_message(
                    chat_id=chat_id,
                    content=content_json,
                    msg_type="text",
                    reply_msg_id=original.metadata.get("msg_id") or original.message_id,
                )
        except Exception:
            logger.exception("Lark send_response failed: chat_id=%s", chat_id)

    # ==================================================================
    # ChannelAdapter 接口 — get_user_info
    # ==================================================================

    async def get_user_info(self, user_id: str) -> ChannelUser | None:
        """通过 lark-oapi Contact API 获取用户信息"""
        if not self._client or not self._running:
            return None

        # Bot 自身查询
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
        """WebSocket 长连接循环"""
        while self._running and self._should_reconnect:
            try:
                domain = (
                    lark.FEISHU_DOMAIN
                    if self._lark_config.domain == "feishu"
                    else lark.LARK_DOMAIN
                )
                ws_kwargs: dict[str, Any] = {
                    "app_id": self._lark_config.app_id,
                    "app_secret": self._lark_config.app_secret,
                    "event_handler": self._dispatch_event,
                    "domain": domain,
                    "auto_reconnect": True,
                }
                if self._lark_config.ws_ping_interval is not None:
                    ws_kwargs["ping_interval"] = self._lark_config.ws_ping_interval
                if self._lark_config.ws_ping_timeout is not None:
                    ws_kwargs["ping_timeout"] = self._lark_config.ws_ping_timeout
                self._ws_client = lark.ws.Client(**ws_kwargs)
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
                delay = self._lark_config.ws_reconnect_interval
                nonce = self._lark_config.ws_reconnect_nonce
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
        """权限门控 — 综合检查群聊策略 / 白名单 / 管理员 / Bot 过滤"""
        is_group = _is_group_chat(chat_id)

        # ── Bot 过滤 ──
        sender_type = getattr(sender_id_obj, "sender_type", "")
        if sender_type == "bot":
            policy = self._lark_config.allow_bots
            if policy == "none":
                return False, "bot_blocked_by_policy"

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

        logger.warning("Lark 未知 group_policy=%s，回退到 open", policy)
        return True, "unknown_policy_fallback_open"

    def _is_bot_mentioned(self, mentions: list[str], sender_open_id: str) -> bool:
        """检查 Bot 是否被 @提及"""
        if not mentions:
            return False

        bot_ids: set[str] = set()
        if self._lark_config.bot_open_id:
            bot_ids.add(self._lark_config.bot_open_id)
        if self._lark_config.bot_user_id:
            bot_ids.add(self._lark_config.bot_user_id)

        if bot_ids:
            return any(m in bot_ids for m in mentions)

        # 未配置任何 Bot ID：有 @提及即放行
        return len(mentions) > 0

    # ==================================================================
    # 事件分发 (Layer 1)
    # ==================================================================

    async def _dispatch_event(self, event: Any) -> None:
        """事件分发 — 统一入口，含 app_id 校验和 reaction/card 路由"""
        event_type = getattr(event, "type", "") or str(event)

        # ── 事件所有权校验（app_id 匹配）──
        if not self._is_event_ownership_valid(event):
            return

        # ── Card Action 事件独立分发 ──
        if event_type == LarkEventType.CARD_ACTION_TRIGGER:
            await self._handle_card_action(event)
            return

        handler = self._event_handlers.get(event_type)
        if handler:
            try:
                await handler(event)
            except Exception as e:
                logger.exception("Lark event handler error [%s]: %s", event_type, e)

    def _is_event_ownership_valid(self, event: Any) -> bool:
        """校验事件的 app_id 是否匹配当前账户（多账户隔离）"""
        expected_app_id = self._lark_config.app_id
        if not expected_app_id:
            return True  # 未配置 app_id，跳过校验

        event_app_id = getattr(event, "app_id", None)
        if event_app_id is None:
            return True  # SDK 未提供 app_id，防御性放行

        if event_app_id != expected_app_id:
            logger.warning(
                "Lark event app_id mismatch, discarding: "
                "expected=%s received=%s",
                expected_app_id, event_app_id,
            )
            return False
        return True

    # ==================================================================
    # 消息接收处理 (Layer 2: 增强 — 串行队列 + 中止快速通道)
    # ==================================================================

    async def _handle_message_receive(self, event: Any) -> None:
        """处理 im.message.receive_v1 事件（含权限门控 + 去重 + 串行队列）"""
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
            thread_id = getattr(message, "thread_id", "") or root_id or None
            create_time = getattr(message, "create_time", "")

            # ── Self-echo hard filter ──
            bot_open_id = self._lark_config.bot_open_id
            if bot_open_id and open_id and open_id == bot_open_id:
                logger.debug("Lark drop self-echo message: msg_id=%s", msg_id)
                return

            # ── 消息去重 ──
            account_id = self._lark_config.account_id
            if msg_id:
                if not self._message_dedup.try_record(msg_id, account_id):
                    logger.debug("Lark 重复消息已忽略: msg_id=%s", msg_id)
                    return

            # ── 过期检查 — reconnect replay ──
            if is_message_expired(str(create_time) if create_time else None):
                logger.debug("Lark 消息已过期: msg_id=%s", msg_id)
                return

            # ── 中止快速通道 ──
            abort_text = extract_raw_text_from_event(event)
            if abort_text and is_likely_abort_text(abort_text):
                queue_key = build_queue_key(account_id, chat_id, thread_id)
                if has_active_task(queue_key):
                    active = get_active_dispatcher(queue_key)
                    if active and active.abort_card:
                        logger.info("Lark abort fast-path: chat=%s text=%r", chat_id, abort_text)
                        if active.abort_controller:
                            active.abort_controller.cancel()
                        asyncio.ensure_future(active.abort_card())

            # ── 串行队列 ──
            status, _task = enqueue_feishu_chat_task(
                account_id=account_id,
                chat_id=chat_id,
                thread_id=thread_id,
                task=lambda: self._process_message(
                    event=event,
                    msg_event=msg_event,
                    message=message,
                    sender=sender,
                    open_id=open_id,
                    chat_id=chat_id,
                    msg_type=msg_type,
                    msg_id=msg_id,
                    root_id=root_id,
                    parent_id=parent_id,
                    thread_id=thread_id,
                ),
            )
            logger.debug(
                "Lark message %s in chat %s%s — %s",
                msg_id, chat_id,
                f" thread {thread_id}" if thread_id else "",
                status,
            )

        except Exception:
            logger.exception("Lark _handle_message_receive 异常")

    async def _process_message(
        self,
        event: Any,
        msg_event: Any,
        message: Any,
        sender: Any,
        open_id: str,
        chat_id: str,
        msg_type: str,
        msg_id: str,
        root_id: str,
        parent_id: str,
        thread_id: str | None,
    ) -> None:
        """消息处理核心逻辑（在串行队列中执行）"""
        sender_id_obj = sender.sender_id

        # ── 权限门控 ──
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

        # @提及门控
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
                "thread_id": thread_id,
                "mentions": mentions,
                "chat_type": "group" if is_group else "p2p",
            },
        )
        self._session_users[chat_id] = open_id
        self._dispatch_message(channel_msg)

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
    # Reaction 事件处理 (增强)
    # ==================================================================

    async def _handle_reaction_created(self, event: Any) -> None:
        """处理 reaction.created_v1 — 对齐 openclaw-lark reaction 路由"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event is None:
                return

            msg_id = getattr(msg_event, "message_id", "")
            operator_open_id = getattr(
                getattr(msg_event, "user_id", None), "open_id", ""
            )
            operator_type = getattr(msg_event, "operator_type", "")
            emoji_type = getattr(
                getattr(msg_event, "reaction_type", None), "emoji_type", ""
            )

            if not msg_id:
                return

            # ── 去重 ──
            dedup_key = f"{msg_id}:reaction:{emoji_type}:{operator_open_id}"
            account_id = self._lark_config.account_id
            if not self._message_dedup.try_record(dedup_key, account_id):
                logger.debug("Lark duplicate reaction: %s", dedup_key)
                return

            # ── 过期检查 ──
            action_time = getattr(msg_event, "action_time", "")
            if is_message_expired(str(action_time) if action_time else None):
                logger.debug("Lark reaction expired: msg_id=%s", msg_id)
                return

            # ── Safety filters ──
            bot_open_id = self._lark_config.bot_open_id
            if operator_type == "app" or operator_open_id == bot_open_id:
                logger.debug("Lark ignoring app/self reaction on %s", msg_id)
                return

            if emoji_type == "Typing":
                return

            # ── Reaction notification mode check ──
            reaction_mode = self._lark_config.reaction_notifications
            if reaction_mode == "off":
                return

            if reaction_mode == "own":
                # Only dispatch reactions on bot's own messages
                if msg_id not in self._bot_message_ids:
                    logger.debug("Lark reaction on non-bot message, ignoring (mode=own)")
                    return

            logger.info(
                "Lark reaction %s on msg %s by %s (mode=%s)",
                emoji_type, msg_id, operator_open_id, reaction_mode,
            )

            # ── 构建 synthetic message 并分发 ──
            synthetic_content = f"[Reaction] {emoji_type}"
            channel_msg = ChannelMessage(
                message_id=f"{msg_id}:reaction:{emoji_type}",
                channel_type=ChannelType.FEISHU,
                user=ChannelUser(user_id=operator_open_id),
                content=synthetic_content,
                session_id=msg_id,  # Use message_id as session for routing
                metadata={
                    "event_type": "reaction_created",
                    "message_id": msg_id,
                    "emoji_type": emoji_type,
                    "operator_open_id": operator_open_id,
                    "is_synthetic": True,
                },
            )
            self._dispatch_message(channel_msg)

        except Exception:
            logger.exception("Lark _handle_reaction_created 异常")

    async def _handle_reaction_deleted(self, event: Any) -> None:
        """处理 reaction.deleted_v1 — 日志记录"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event is None:
                return

            msg_id = getattr(msg_event, "message_id", "")
            emoji_type = getattr(
                getattr(msg_event, "reaction_type", None), "emoji_type", ""
            )
            logger.debug(
                "Lark reaction deleted: msg_id=%s emoji=%s", msg_id, emoji_type,
            )
        except Exception:
            pass

    # ==================================================================
    # Bot 群聊成员事件处理 (增强)
    # ==================================================================

    async def _handle_bot_added_to_chat(self, event: Any) -> None:
        """处理 bot.added_to_chat — 记录日志"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event:
                chat_id = getattr(msg_event, "chat_id", "")
                operator_id = getattr(
                    getattr(msg_event, "operator_id", None), "open_id", ""
                )
                logger.info(
                    "Lark bot added to chat: chat_id=%s operator=%s",
                    chat_id, operator_id,
                )
        except Exception:
            pass

    async def _handle_bot_removed_from_chat(self, event: Any) -> None:
        """处理 bot.removed_from_chat — 记录日志"""
        try:
            msg_event = getattr(event, "event", None)
            if msg_event:
                chat_id = getattr(msg_event, "chat_id", "")
                operator_id = getattr(
                    getattr(msg_event, "operator_id", None), "open_id", ""
                )
                logger.info(
                    "Lark bot removed from chat: chat_id=%s operator=%s",
                    chat_id, operator_id,
                )
        except Exception:
            pass

    # ==================================================================
    # Card Action 事件处理 (增强)
    # ==================================================================

    async def _handle_card_action(self, event: Any) -> None:
        """处理 card.action.trigger — 交互式卡片回调"""
        try:
            account_id = self._lark_config.account_id

            # 构建 send 回调（使用 adapter 自身的 send 方法）
            async def _send_card(chat_id, card, reply_to_message_id=None, **kwargs):
                return await self._send_card_message(
                    chat_id=chat_id,
                    card=card,
                    reply_msg_id=reply_to_message_id,
                )

            async def _update_card(message_id, card, **kwargs):
                return await self._update_card_message(
                    message_id=message_id,
                    card=card,
                )

            async def _send_msg(to, text, reply_to_message_id=None, account_id=None):
                content = json.dumps({"text": text})
                return await self._send_message(
                    chat_id=to,
                    content=content,
                    msg_type="text",
                    reply_msg_id=reply_to_message_id,
                )

            dispatcher = get_interactive_dispatcher()
            result = await dispatcher.dispatch(
                data=event,
                account_id=account_id,
                send_message_fn=_send_msg,
                update_card_fn=_update_card,
                send_card_fn=_send_card,
            )

            if result:
                logger.debug("Lark card action dispatched: result=%s", result)

        except Exception:
            logger.exception("Lark _handle_card_action 异常")

    # ==================================================================
    # API 调用 — 发送消息 (Layer 1: lark-oapi)
    # ==================================================================

    async def _handle_vc_meeting_invited(self, event: Any) -> None:
        """处理 vc.bot.meeting_invited_v1 — 对齐 openclaw-lark VC handler"""
        try:
            msg_event = getattr(event, "event", None) or event
            meeting_no = getattr(getattr(msg_event, "meeting", None), "meeting_no", "") or ""
            topic = getattr(getattr(msg_event, "meeting", None), "topic", "") or ""
            inviter = getattr(msg_event, "inviter", None)
            inviter_name = getattr(inviter, "user_name", "") if inviter else ""
            logger.info(
                "Lark vc meeting invited: meeting_no=%s topic=%s inviter=%s",
                meeting_no, topic, inviter_name,
            )
        except Exception:
            logger.exception("Lark _handle_vc_meeting_invited 异常")

    async def _handle_drive_comment_add(self, event: Any) -> None:
        """处理 drive.notice.comment_add_v1 — 对齐 openclaw-lark comment handler"""
        try:
            msg_event = getattr(event, "event", None) or event
            file_token = getattr(msg_event, "file_token", "") or ""
            file_type = getattr(msg_event, "file_type", "") or ""
            comment_id = getattr(msg_event, "comment_id", "") or ""
            logger.info(
                "Lark drive comment: file=%s type=%s comment_id=%s",
                file_token, file_type, comment_id,
            )
        except Exception:
            logger.exception("Lark _handle_drive_comment_add 异常")

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

        # ── 规范化 receive_id_type ──
        receive_id_type = resolve_receive_id_type(chat_id)

        try:
            body = CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type(msg_type) \
                .content(content) \
                .build()

            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
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

            # ── 记录 bot 发送的消息 ID（用于 reaction own mode）──
            if msg_id:
                self._track_bot_message(msg_id)

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

    async def _send_card_message(
        self,
        chat_id: str,
        card: dict,
        reply_msg_id: str | None = None,
    ) -> str:
        """发送交互式卡片消息"""
        import json

        receive_id_type = resolve_receive_id_type(chat_id)

        # Build card payload
        card_content = json.dumps(card, ensure_ascii=False)

        try:
            body = CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type("interactive") \
                .content(card_content) \
                .build()

            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(body) \
                .build()

            response = await asyncio.to_thread(
                self._client.im.v1.message.create, request
            )

            if response.code != 0:
                logger.error("Lark send_card failed: code=%s msg=%s", response.code, response.msg)
                return ""

            msg_id = getattr(response.data, "message_id", "") if response.data else ""
            if msg_id:
                self._track_bot_message(msg_id)
            logger.debug("Lark card sent: msg_id=%s chat_id=%s", msg_id, chat_id)
            return msg_id

        except Exception:
            logger.exception("Lark send_card exception")
            return ""

    async def _update_card_message(
        self,
        message_id: str,
        card: dict,
    ) -> bool:
        """更新已发送的卡片消息"""
        import json
        from lark_oapi.api.im.v1 import (
            PatchMessageRequest,
            PatchMessageRequestBody,
        )

        card_content = json.dumps(card, ensure_ascii=False)

        try:
            body = PatchMessageRequestBody.builder() \
                .content(card_content) \
                .build()
            request = PatchMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(body) \
                .build()

            response = await asyncio.to_thread(
                self._client.im.v1.message.patch, request
            )
            if response.code != 0:
                logger.debug("Lark update_card failed: code=%s", response.code)
                return False
            return True

        except Exception:
            logger.debug("Lark update_card exception", exc_info=True)
            return False

    # ==================================================================
    # Bot 消息追踪（用于 reaction own mode）
    # ==================================================================

    def _track_bot_message(self, msg_id: str) -> None:
        """记录 bot 发送的消息 ID"""
        self._bot_message_ids[msg_id] = time.time()
        while len(self._bot_message_ids) > self._bot_msg_max:
            self._bot_message_ids.popitem(last=False)

    # ==================================================================
    # API 调用 — 媒体消息
    # ==================================================================

    async def send_image(
        self, chat_id: str, image_data: bytes, image_type: str = "message"
    ) -> str:
        """发送图片消息"""
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
        """发送文件消息"""
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
        """从飞书消息体中提取文本内容"""
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
                result = parse_feishu_post_payload(body)
                return result.text_content
            except Exception:
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
        """将文本转换为飞书 Post 格式的 JSON"""
        try:
            result = _build_markdown_post_payload(text)
            return str(result)
        except Exception:
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
        """解析发送目标 chat_id（含 target 规范化）"""
        chat_id = str(message.metadata.get("chat_id", ""))
        if not chat_id:
            chat_id = message.session_id or ""

        # ── 规范化 ——
        normalized = normalize_feishu_target(chat_id)
        return normalized or chat_id

    # ==================================================================
    # HTTP Webhook 兼容
    # ==================================================================

    async def handle_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        """处理 HTTP Webhook 回调"""
        # URL 验证
        if body.get("type") == LarkEventType.URL_VERIFICATION:
            return {"challenge": body.get("challenge", "")}

        if body.get("encrypt"):
            logger.warning("Lark: 加密 Webhook 推送暂不支持")
            return {"code": 400, "msg": "encrypted payloads not supported"}

        return {}

    @staticmethod
    def verify_webhook_signature(
        encrypt_key: str,
        timestamp: str,
        nonce: str,
        signature: str,
        body_bytes: bytes,
    ) -> bool:
        """验证飞书 Webhook 签名"""
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
