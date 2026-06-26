"""
Onboarding module — pairing-triggered setup and OAuth authorization.

Aligns with larksuite/openclaw-lark:
  - src/tools/onboarding-auth.ts — OAuth Device Flow for app owner
  - src/channel/onboarding.ts — Interactive wizard adapter

Flow:
  1. Bot is paired (approved) by a user
  2. Check if the user is the app owner
  3. If yes, send a welcome/interactive onboarding card
  4. Optionally trigger batch OAuth for granted scopes
  5. Track onboarding state per account

The welcome card provides:
  - Quick-start guide for the user
  - Configuration summary (domain, group policy, etc.)
  - Links to documentation
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger("clawhermes.lark.onboarding")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ONBOARDING_STATE_FILE = "feishu_onboarding_state.json"

# Track which accounts have completed onboarding
_onboarding_state: dict[str, dict[str, Any]] = {}
_onboarding_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Onboarding state management
# ---------------------------------------------------------------------------


def _get_lock(account_id: str) -> asyncio.Lock:
    """Get or create a per-account lock for onboarding operations."""
    if account_id not in _onboarding_locks:
        _onboarding_locks[account_id] = asyncio.Lock()
    return _onboarding_locks[account_id]


def is_onboarding_complete(account_id: str) -> bool:
    """Check if onboarding has been completed for an account."""
    return _onboarding_state.get(account_id, {}).get("complete", False)


def mark_onboarding_complete(account_id: str) -> None:
    """Mark onboarding as complete for an account."""
    _onboarding_state.setdefault(account_id, {})["complete"] = True
    _onboarding_state[account_id]["completed_at"] = time.time()


def reset_onboarding(account_id: str) -> None:
    """Reset onboarding state for an account."""
    _onboarding_state.pop(account_id, None)


# ---------------------------------------------------------------------------
# Welcome Card Builder
# ---------------------------------------------------------------------------


def build_welcome_card(
    bot_name: str = "ClawHermes",
    domain: str = "feishu",
    group_policy: str = "allowlist",
    app_id: str = "",
) -> dict[str, Any]:
    """
    Build the onboarding welcome card.

    This is an interactive card shown when the bot is first paired.
    It provides setup guidance and configuration overview.
    """
    domain_label = "飞书" if domain == "feishu" else "Lark"
    policy_labels = {
        "allowlist": "白名单模式（仅允许名单内用户）",
        "open": "开放模式（任何群聊可交互）",
        "disabled": "已禁用群聊",
        "admin_only": "仅管理员",
        "blacklist": "黑名单模式",
    }
    policy_label = policy_labels.get(group_policy, group_policy)

    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"👋 **{bot_name}** 已成功连接！\n\n"
                f"我已准备好在 **{domain_label}** 上为你服务。\n\n"
                f"**当前配置：**\n"
                f"- 平台：{domain_label}\n"
                f"- 群聊策略：{policy_label}\n"
                f"{'- App ID: ' + app_id[:12] + '***' if app_id else ''}\n\n"
                f"**快速上手：**\n"
                f"- 在 **私聊** 中直接向我发送消息\n"
                f"- 在 **群聊** 中 @我 然后发送消息\n"
                f"- 使用 `/help` 查看可用命令"
            ),
        },
        {
            "tag": "hr",
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "💡 提示：你可以在 ClawHermes 配置中修改群聊策略、白名单等设置。",
                }
            ],
        },
    ]

    return {
        "config": {
            "wide_screen_mode": True,
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"🤖 {bot_name} 已就绪",
            },
            "template": "wathet",
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Onboarding trigger
# ---------------------------------------------------------------------------


async def trigger_onboarding(
    adapter: Any,
    user_open_id: str,
    account_id: str = "default",
    app_id: str = "",
    bot_name: str = "ClawHermes",
    domain: str = "feishu",
    group_policy: str = "allowlist",
) -> bool:
    """
    Trigger the onboarding flow when a user pairs with the bot.

    This should be called when:
    - A user is approved (added to allowlist / paired)
    - The bot is added to a new chat

    Args:
        adapter: LarkAdapter instance (for sending messages)
        user_open_id: The open_id of the user who triggered pairing
        account_id: Account identifier
        app_id: App ID for display
        bot_name: Bot display name
        domain: Platform domain (feishu/lark)
        group_policy: Current group policy setting

    Returns:
        True if onboarding was triggered, False if already complete.
    """
    lock = _get_lock(account_id)

    async with lock:
        if is_onboarding_complete(account_id):
            logger.debug("Onboarding already complete for account=%s", account_id)
            return False

        logger.info(
            "Triggering onboarding for user=%s account=%s",
            user_open_id[:12] + "***", account_id,
        )

        try:
            # Build welcome card
            card = build_welcome_card(
                bot_name=bot_name,
                domain=domain,
                group_policy=group_policy,
                app_id=app_id,
            )

            # Send as interactive card via the adapter
            if hasattr(adapter, "_send_card_message"):
                result = await adapter._send_card_message(
                    chat_id=user_open_id,
                    card=card,
                )
                if result:
                    logger.info(
                        "Onboarding welcome card sent to user=%s",
                        user_open_id[:12] + "***",
                    )

            # Mark as complete
            mark_onboarding_complete(account_id)
            return True

        except Exception:
            logger.exception("Onboarding failed for account=%s", account_id)
            return False


async def trigger_onboarding_auth(
    adapter: Any,
    user_open_id: str,
    account_id: str = "default",
) -> bool:
    """
    Trigger the OAuth authorization onboarding flow.

    Checks if the user is the app owner and initiates OAuth if so.
    This is called after pairing approval to pre-authorize scopes.

    Currently a stub — full OAuth Device Flow implementation requires
    the Feishu OAuth API which may need additional app configuration.
    """
    logger.info(
        "Onboarding OAuth triggered for user=%s account=%s (stub)",
        user_open_id[:12] + "***", account_id,
    )

    # TODO: Implement full OAuth Device Flow:
    # 1. Check if user is app owner (via getAppOwnerFallback)
    # 2. Get granted scopes (via getAppGrantedScopes)
    # 3. Batch authorize scopes (via OAuth Device Flow API)
    # 4. Track authorization state

    mark_onboarding_complete(account_id)
    return True


# ---------------------------------------------------------------------------
# Onboarding card action handler
# ---------------------------------------------------------------------------


async def handle_onboarding_card_action(
    action: str,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Handle interactive card actions from the onboarding card.

    Args:
        action: The action value from the card button
        context: Context dict with sender info, chat info, etc.

    Returns:
        Response dict (toast) or None if action not recognized.
    """
    if action == "onboarding_dismiss":
        account_id = context.get("account_id", "default")
        mark_onboarding_complete(account_id)
        return {"toast": {"type": "info", "content": "已关闭引导，随时可以 @我 开始对话"}}

    if action == "onboarding_help":
        return {
            "toast": {
                "type": "info",
                "content": "发送 /help 查看完整命令列表",
            }
        }

    return None


# ---------------------------------------------------------------------------
# Onboarding state persistence helpers
# ---------------------------------------------------------------------------


def load_onboarding_state(data_dir: str = "") -> None:
    """Load onboarding state from disk (if applicable)."""
    try:
        from pathlib import Path

        path = Path(data_dir) / ONBOARDING_STATE_FILE if data_dir else Path(ONBOARDING_STATE_FILE)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            _onboarding_state.update(data)
            logger.debug("Loaded onboarding state: %d accounts", len(data))
    except Exception:
        logger.debug("Failed to load onboarding state", exc_info=True)


def save_onboarding_state(data_dir: str = "") -> None:
    """Save onboarding state to disk."""
    try:
        from pathlib import Path
        import tempfile
        import os

        path = Path(data_dir) / ONBOARDING_STATE_FILE if data_dir else Path(ONBOARDING_STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, suffix=".tmp"
        )
        try:
            json.dump(_onboarding_state, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, str(path))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("Failed to save onboarding state", exc_info=True)


# ---------------------------------------------------------------------------
# Device Flow onboarding — 扫码授权
# ---------------------------------------------------------------------------


async def start_device_flow_onboarding(
    app_id: str,
    app_secret: str,
    brand: str = "feishu",
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """
    发起 Device Flow 授权流程.

    返回包含 device_code, user_code, verification_uri 等信息，
    调用方可据此生成二维码展示给用户。

    Returns:
        dict with keys: device_code, user_code, verification_uri,
        verification_uri_complete, expires_in, interval,
        qr_text (卡片中展示的 markdown 文本)
    """
    from clawhermes_lark.openclaw_lark.core.device_flow import (
        request_device_authorization,
        build_auth_card_qr_text,
    )

    try:
        auth_resp = await request_device_authorization(
            app_id=app_id,
            app_secret=app_secret,
            brand=brand,
            scopes=scopes,
        )

        qr_text = build_auth_card_qr_text(auth_resp)

        return {
            "ok": True,
            "device_code": auth_resp.device_code,
            "user_code": auth_resp.user_code,
            "verification_uri": auth_resp.verification_uri,
            "verification_uri_complete": auth_resp.verification_uri_complete,
            "expires_in": auth_resp.expires_in,
            "interval": auth_resp.interval,
            "qr_text": qr_text,
        }
    except Exception as e:
        logger.exception("Device flow onboarding failed")
        return {"ok": False, "error": str(e)}


async def complete_device_flow_onboarding(
    app_id: str,
    app_secret: str,
    brand: str,
    device_code: str,
    interval: int = 5,
    expires_in: int = 300,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """
    轮询完成 Device Flow，获取 access_token.

    在调用 start_device_flow_onboarding 之后，将此函数作为后台任务
    运行以轮询授权结果。

    Returns:
        dict with ok, token (access_token, refresh_token, ...) or error
    """
    from clawhermes_lark.openclaw_lark.core.device_flow import poll_device_token

    result = await poll_device_token(
        app_id=app_id,
        app_secret=app_secret,
        brand=brand,
        device_code=device_code,
        interval=interval,
        expires_in=expires_in,
        cancel_event=cancel_event,
    )

    if result.ok and result.token:
        return {
            "ok": True,
            "access_token": result.token.access_token,
            "refresh_token": result.token.refresh_token,
            "expires_in": result.token.expires_in,
            "scope": result.token.scope,
        }
    else:
        return {
            "ok": False,
            "error": result.error,
            "message": result.message,
        }


def build_device_flow_card(
    user_code: str,
    verification_uri: str,
    verification_uri_complete: str = "",
    expires_minutes: int = 5,
) -> dict[str, Any]:
    """
    构建 Device Flow 授权卡片（可发送到飞书).

    包含授权链接和手动输入指引.
    """
    uri = verification_uri_complete or f"{verification_uri}?user_code={user_code}"

    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"📱 **授权机器人访问 Feishu**\n\n"
                f"请点击下方链接完成授权（有效期 {expires_minutes} 分钟）：\n\n"
                f"🔗 [{uri}]({uri})\n\n"
                f"或手动输入：\n"
                f"- 授权码：**`{user_code}`**\n"
                f"- 验证地址：{verification_uri}"
            ),
        },
        {
            "tag": "hr",
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "💡 授权完成后 Bot 即可访问你在飞书的文档、日历等资源",
                }
            ],
        },
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔐 授权机器人"},
            "template": "blue",
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Setup Wizard — 两种创建方式（对齐 setup-surface.ts）
# ---------------------------------------------------------------------------


async def run_setup_qr_code_flow(
    brand: str = "feishu",
    show_qr: bool = True,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """
    方式一：扫码创建 — 完整的 App Registration Flow.

    1. init → 检查环境
    2. begin → 生成 QR 码（自动创建"个人 Agent"应用）
    3. 展示 QR 码（终端或卡片）
    4. poll → 轮询 → 返回 appId + appSecret + openId

    Returns:
        {"ok": True, "app_id": "...", "app_secret": "...", "open_id": "..."}
        或 {"ok": False, "error": "..."}
    """
    from clawhermes_lark.openclaw_lark.core.app_registration import (
        app_registration_init,
        app_registration_begin,
        app_registration_poll,
        build_qr_guide_text,
    )

    # Step 1: Init
    init = await app_registration_init(brand)
    if not init.ok:
        return {"ok": False, "error": f"环境检查失败: {init.error}"}
    if not init.supports_client_secret:
        return {
            "ok": False,
            "error": "当前环境不支持自动创建应用，请使用手动配置方式",
            "fallback_to_manual": True,
        }

    # Step 2: Begin
    begin = await app_registration_begin(brand)
    if not begin.ok:
        return {"ok": False, "error": f"创建失败: {begin.error}"}

    # Step 3: 展示 QR 码
    if show_qr:
        qr_text = build_qr_guide_text(begin)
        # 调用方可以将 qr_text 发送到终端或飞书卡片
        logger.info("QR code for app registration:\n%s", qr_text)

    # Step 4: Poll
    poll = await app_registration_poll(
        brand=brand,
        device_code=begin.device_code,
        interval=begin.interval,
        expire_in=begin.expire_in,
        cancel_event=cancel_event,
    )

    if not poll.ok:
        return {"ok": False, "error": poll.error}

    return {
        "ok": True,
        "app_id": poll.client_id,
        "app_secret": poll.client_secret,
        "open_id": poll.open_id,
        "mode": "qr_code",
    }


async def run_setup_manual_flow(
    app_id: str,
    app_secret: str,
    client=None,
) -> dict[str, Any]:
    """
    方式二：手动配置 — 用户已在 open.feishu.cn 创建应用.

    填入 App ID / App Secret 后：
      1. 验证凭证（probe）
      2. 自动获取 owner open_id
      3. 应用自动安全策略

    Returns:
        {"ok": True, "open_id": "..."} 或 {"ok": False, "error": "..."}
    """
    from clawhermes_lark.openclaw_lark.core.app_registration import (
        get_app_owner_open_id,
    )
    from clawhermes_lark.adapter.client import LarkClient

    if not app_id or not app_secret:
        return {"ok": False, "error": "缺少 App ID 或 App Secret"}

    # 验证凭证
    try:
        if client is None:
            lc = LarkClient.from_credentials(app_id=app_id, app_secret=app_secret)
        else:
            lc = client

        identity = await lc.get_bot_identity()
        if identity is None:
            return {"ok": False, "error": "凭证验证失败，请检查 App ID / App Secret"}

        # 获取 owner open_id
        open_id = await get_app_owner_open_id(lc.sdk if hasattr(lc, 'sdk') else lc._client)

        return {
            "ok": True,
            "open_id": open_id,
            "bot_name": identity.name,
            "mode": "manual",
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


def build_setup_mode_card(show_qr_url: str = "") -> dict[str, Any]:
    """
    构建配置模式选择卡片 — "扫码创建" 或 "手动配置".
    """
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                "## 🤖 配置飞书 Bot\n\n"
                "请选择创建方式：\n\n"
                "**方式一：扫码创建（推荐）**\n"
                "使用飞书 App 扫描二维码，自动创建企业自建应用\n\n"
                "**方式二：手动配置**\n"
                "前往 open.feishu.cn 手动创建应用，填入凭证"
            ),
        },
    ]

    if show_qr_url:
        elements.append({
            "tag": "markdown",
            "content": f"🔗 [扫码创建]({show_qr_url})",
        })

    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "📱 扫码创建"},
                "type": "primary",
                "value": {"action": "setup:qr_code"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "⌨️ 手动配置"},
                "type": "default",
                "value": {"action": "setup:manual"},
            },
        ],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⚙️ 配置飞书 Bot"},
            "template": "blue",
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Onboarding Config Helpers — 对齐 onboarding-config.ts
# ---------------------------------------------------------------------------


def parse_allow_from_input(raw_input: str) -> list[str]:
    """
    解析 allow_from 输入字符串.

    支持逗号、空格、换行分隔.
    自动识别 feishu: 和 user: 前缀并剥离.
    """
    import re

    # 按逗号、空格、换行拆分
    parts = re.split(r'[,\s\n]+', raw_input.strip())
    result = []

    for part in parts:
        part = part.strip().lower()
        if not part:
            continue

        # 剥离 feishu: / user: / open_id: 前缀
        for prefix in ("feishu:", "user:", "open_id:"):
            if part.startswith(prefix):
                part = part[len(prefix):]
                break

        if part and (part.startswith("ou_") or part.startswith("on_")):
            result.append(part)

    return result


def set_feishu_allow_from(config: dict, allow_from: list[str]) -> dict:
    """设置 Feishu DM 白名单."""
    channels = config.get("channels", {})
    feishu = dict(channels.get("feishu", {}))
    feishu["allowFrom"] = allow_from
    feishu["dmPolicy"] = "allowlist"
    channels["feishu"] = feishu
    config["channels"] = channels
    return config


def set_feishu_group_policy(config: dict, policy: str) -> dict:
    """设置群聊策略."""
    channels = config.get("channels", {})
    feishu = dict(channels.get("feishu", {}))
    feishu["groupPolicy"] = policy
    channels["feishu"] = feishu
    config["channels"] = channels
    return config


def set_feishu_group_allow_from(config: dict, group_allow_from: list[str]) -> dict:
    """设置群聊白名单."""
    channels = config.get("channels", {})
    feishu = dict(channels.get("feishu", {}))
    feishu["groupAllowFrom"] = group_allow_from
    channels["feishu"] = feishu
    config["channels"] = channels
    return config
