"""
Security and safety checks for Feishu/Lark channel operations.

Aligns with larksuite/openclaw-lark src/core/security-check.ts:
  - Multi-account cross-tenant isolation warnings
  - Group policy safety advisories
  - Bot-fencing detection
  - Allow-from policy validation

Provides warnings collection used during setup and diagnostics.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("clawhermes.lark.security")


def collect_security_warnings(
    config: dict | None = None,
    account_id: str = "default",
    feishu_cfg: dict | None = None,
) -> list[str]:
    """
    Collect security warnings for a Feishu account configuration.

    Returns a list of human-readable warning strings.
    Empty list means no concerns.
    """
    warnings: list[str] = []

    if not feishu_cfg and config:
        from clawhermes_lark.accounts import get_lark_account
        account = get_lark_account(config, account_id)
        feishu_cfg = account.config

    if not feishu_cfg:
        return warnings

    # 1. Group policy warning — "open" allows any group to interact
    group_policy = feishu_cfg.get("groupPolicy") or feishu_cfg.get("group_policy", "allowlist")
    if group_policy == "open":
        warnings.append(
            f"- Feishu[{account_id}] groups: groupPolicy=\"open\" allows any "
            "group to interact (mention-gated). To restrict which groups are "
            "allowed, set groupPolicy=\"allowlist\" and list group IDs. To "
            "restrict which senders can trigger the bot, set allowed_group_users."
        )

    # 2. No encryption key for Webhook mode
    connection_mode = feishu_cfg.get("connectionMode") or feishu_cfg.get("connection_mode", "websocket")
    if connection_mode == "webhook":
        encrypt_key = feishu_cfg.get("encryptKey") or feishu_cfg.get("encrypt_key", "")
        if not encrypt_key:
            warnings.append(
                f"- Feishu[{account_id}] webhook mode: encrypt_key is not set. "
                "Webhook payloads will not be verified for authenticity, making "
                "the endpoint vulnerable to spoofed requests. Set encrypt_key in "
                "your Feishu app's event subscription settings."
            )

    # 3. No verification token
    vt = feishu_cfg.get("verificationToken") or feishu_cfg.get("verification_token", "")
    if not vt:
        warnings.append(
            f"- Feishu[{account_id}] verification_token is not set. "
            "This is used as a second authentication layer for event callbacks."
        )

    # 4. Bot interaction policy
    allow_bots = feishu_cfg.get("allowBots") or feishu_cfg.get("allow_bots", "none")
    if allow_bots == "all":
        warnings.append(
            f"- Feishu[{account_id}] allow_bots=\"all\" allows any bot to trigger "
            "this bot, which may cause bot-to-bot loops. Set to \"mentions\" or "
            "\"none\" to restrict."
        )

    return warnings


def collect_isolation_warnings(config: dict | None = None) -> list[str]:
    """
    Check for cross-tenant isolation issues in multi-account configs.

    When multiple Feishu accounts exist, verify they belong to the same
    tenant or have proper isolation configured.
    """
    warnings: list[str] = []

    if not config:
        return warnings

    try:
        from clawhermes_lark.accounts import get_lark_account_ids, get_lark_account
        ids = get_lark_account_ids(config)
        if len(ids) <= 1:
            return warnings

        # Collect app IDs
        app_ids: list[str] = []
        for aid in ids:
            account = get_lark_account(config, aid)
            if account.app_id:
                app_ids.append(account.app_id)

        # Check for duplicate app IDs (same app, different accounts)
        seen: set[str] = set()
        for app_id in app_ids:
            if app_id in seen:
                warnings.append(
                    "- Multiple Feishu accounts share the same app_id. "
                    "This may cause event routing issues. Ensure each account "
                    "has a unique app_id or use a single account."
                )
                break
            seen.add(app_id)

    except Exception:
        logger.debug("Failed to check isolation", exc_info=True)

    return warnings


def validate_allow_from(allow_from: list[str]) -> list[str]:
    """
    Validate and normalize an allow-from entry list.

    Normalizes entries to lowercase and filters empty strings.
    """
    return [
        str(entry).strip().lower()
        for entry in allow_from
        if str(entry).strip()
    ]


def is_feishu_id_valid(raw_id: str) -> bool:
    """Basic validation that a string looks like a valid Feishu ID."""
    if not raw_id or not isinstance(raw_id, str):
        return False
    from clawhermes_lark.targets import detect_id_type
    return detect_id_type(raw_id) is not None
