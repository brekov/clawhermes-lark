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
from collections import OrderedDict
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
