"""
Multi-account support for Feishu/Lark channel adapter.

Aligns with larksuite/openclaw-lark src/core/accounts.ts:
  - Resolve accounts from config (default + named)
  - Account enable/disable
  - Per-account config merging
  - Multi-account isolation (separate clients, dedup stores)

Supports the `accounts` config structure:
  channels.feishu.accounts.<accountId> = { appId, appSecret, ... }
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("clawhermes.lark.accounts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ACCOUNT_ID = "default"

# ---------------------------------------------------------------------------
# Account type
# ---------------------------------------------------------------------------


@dataclass
class LarkAccount:
    """A resolved Feishu/Lark account with merged config."""

    account_id: str = DEFAULT_ACCOUNT_ID
    enabled: bool = True
    configured: bool = False

    # Credentials
    app_id: str | None = None
    app_secret: str | None = None
    encrypt_key: str = ""
    verification_token: str = ""

    # Brand
    brand: str = "feishu"  # "feishu" | "lark"

    # Config overrides
    config: dict[str, Any] = field(default_factory=dict)

    # Display
    name: str = ""

    @property
    def is_configured(self) -> bool:
        return self.configured and bool(self.app_id) and bool(self.app_secret)


# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------


def get_lark_account_ids(config: dict[str, Any]) -> list[str]:
    """
    Get all configured Lark account IDs from the config.

    Config structure:
      {
        "channels": {
          "feishu": {
            "appId": "...",
            "appSecret": "...",
            "accounts": {
              "account2": { "appId": "...", "appSecret": "..." }
            }
          }
        }
      }
    """
    feishu_cfg = _get_feishu_config(config)
    if not feishu_cfg:
        return []

    ids = [DEFAULT_ACCOUNT_ID]

    accounts = feishu_cfg.get("accounts", {})
    if isinstance(accounts, dict):
        for aid in accounts:
            if aid != DEFAULT_ACCOUNT_ID:
                ids.append(aid)

    return ids


def get_lark_account(
    config: dict[str, Any],
    account_id: str | None = None,
) -> LarkAccount:
    """
    Resolve a single Lark account from config.

    Merges top-level feishu config with per-account overrides.
    """
    aid = account_id or DEFAULT_ACCOUNT_ID
    feishu_cfg = _get_feishu_config(config) or {}

    if aid == DEFAULT_ACCOUNT_ID:
        # Use top-level config
        app_id = feishu_cfg.get("appId") or feishu_cfg.get("app_id", "")
        app_secret = feishu_cfg.get("appSecret") or feishu_cfg.get("app_secret", "")
        return LarkAccount(
            account_id=aid,
            enabled=feishu_cfg.get("enabled", True),
            configured=bool(app_id and app_secret),
            app_id=app_id,
            app_secret=app_secret,
            encrypt_key=feishu_cfg.get("encryptKey", ""),
            verification_token=feishu_cfg.get("verificationToken", ""),
            brand=feishu_cfg.get("brand") or feishu_cfg.get("domain", "feishu"),
            config=feishu_cfg,
            name=feishu_cfg.get("name", ""),
        )

    # Named account — merge top-level + per-account overrides
    accounts = feishu_cfg.get("accounts", {})
    account_cfg = accounts.get(aid, {}) if isinstance(accounts, dict) else {}

    # Merge: top-level config as base, account-specific overrides
    merged = dict(feishu_cfg)
    merged.update(account_cfg)
    merged.pop("accounts", None)  # Don't pass accounts dict downstream

    app_id = merged.get("appId") or merged.get("app_id", "")
    app_secret = merged.get("appSecret") or merged.get("app_secret", "")

    return LarkAccount(
        account_id=aid,
        enabled=merged.get("enabled", True),
        configured=bool(app_id and app_secret),
        app_id=app_id,
        app_secret=app_secret,
        encrypt_key=merged.get("encryptKey", ""),
        verification_token=merged.get("verificationToken", ""),
        brand=merged.get("brand") or merged.get("domain", "feishu"),
        config=merged,
        name=merged.get("name", ""),
    )


def get_default_lark_account_id(config: dict[str, Any]) -> str:
    """Get the default account ID (first configured one)."""
    ids = get_lark_account_ids(config)
    return ids[0] if ids else DEFAULT_ACCOUNT_ID


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


def set_account_enabled(
    config: dict[str, Any],
    account_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Set the enabled flag on a Lark account (returns updated config)."""
    return _merge_account_config(config, account_id, {"enabled": enabled})


def delete_account(
    config: dict[str, Any],
    account_id: str,
) -> dict[str, Any]:
    """Delete a Lark account from config (returns updated config)."""
    feishu_cfg = _get_feishu_config(config)
    if not feishu_cfg:
        return config

    if account_id == DEFAULT_ACCOUNT_ID:
        # Delete entire feishu config
        channels = dict(config.get("channels", {}))
        channels.pop("feishu", None)
        result = dict(config)
        result["channels"] = channels
        return result

    # Delete specific named account
    accounts = dict(feishu_cfg.get("accounts", {}))
    accounts.pop(account_id, None)
    new_feishu = dict(feishu_cfg)
    if accounts:
        new_feishu["accounts"] = accounts
    else:
        new_feishu.pop("accounts", None)

    channels = dict(config.get("channels", {}))
    channels["feishu"] = new_feishu
    result = dict(config)
    result["channels"] = channels
    return result


def apply_account_config(
    config: dict[str, Any],
    account_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Apply an arbitrary config patch to a Lark account."""
    return _merge_account_config(config, account_id, patch)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_feishu_config(config: dict[str, Any]) -> dict[str, Any] | None:
    """Extract feishu config section from the full config."""
    channels = config.get("channels", {})
    if not isinstance(channels, dict):
        return None
    return channels.get("feishu")


def _merge_account_config(
    config: dict[str, Any],
    account_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge a config patch into a specific account."""
    feishu_cfg = _get_feishu_config(config) or {}

    if account_id == DEFAULT_ACCOUNT_ID:
        new_feishu = dict(feishu_cfg)
        new_feishu.update(patch)
    else:
        accounts = dict(feishu_cfg.get("accounts", {}))
        existing = dict(accounts.get(account_id, {}))
        existing.update(patch)
        accounts[account_id] = existing
        new_feishu = dict(feishu_cfg)
        new_feishu["accounts"] = accounts

    channels = dict(config.get("channels", {}))
    channels["feishu"] = new_feishu
    result = dict(config)
    result["channels"] = channels
    return result
