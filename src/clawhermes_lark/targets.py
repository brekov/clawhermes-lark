"""
Feishu target ID parsing and formatting utilities.

Aligns with larksuite/openclaw-lark src/core/targets.ts:
  - Detect ID type from prefix patterns (oc_ = chat, ou_ = open user)
  - Normalize raw targets (strip routing prefixes: chat:, user:, open_id:, feishu:)
  - Format targets with routing prefixes
  - Resolve receive_id_type for API calls
  - Route metadata fragment parsing (#reply_to=...&thread_id=...)

Feishu uses several namespaced identifier prefixes:
  - oc_*  — chat (group / DM) IDs
  - ou_*  — open user IDs
  - plain alphanumeric — tenant user IDs
"""
from __future__ import annotations

import re
from typing import Literal

# ---------------------------------------------------------------------------
# Known prefix patterns
# ---------------------------------------------------------------------------

CHAT_PREFIX = "oc_"
OPEN_ID_PREFIX = "ou_"

# Canonical routing prefixes used inside the channel adapter
TAG_CHAT = "chat:"
TAG_USER = "user:"
TAG_OPEN_ID = "open_id:"
TAG_FEISHU = "feishu:"

# Route metadata fragment keys
ROUTE_META_FRAGMENT_REPLY_TO = "__feishu_reply_to"
ROUTE_META_FRAGMENT_THREAD_ID = "__feishu_thread_id"

# ---------------------------------------------------------------------------
# ID type
# ---------------------------------------------------------------------------

FeishuIdType = Literal["open_id", "user_id", "union_id", "chat_id"]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_id_type(raw_id: str) -> FeishuIdType | None:
    """
    Detect the Feishu ID type from a raw identifier string.

    Returns None when the string does not match any known pattern.
    """
    if not raw_id:
        return None
    if raw_id.startswith(CHAT_PREFIX):
        return "chat_id"
    if raw_id.startswith(OPEN_ID_PREFIX):
        return "open_id"
    # Plain alphanumeric strings (no prefix) → tenant user IDs
    if re.fullmatch(r"[a-zA-Z0-9]+", raw_id):
        return "user_id"
    return None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_feishu_target(raw: str) -> str | None:
    """
    Strip routing prefixes from a raw target string, returning the bare
    Feishu identifier. Returns None when input is empty or falsy.
    """
    if not raw:
        return None

    parsed = parse_feishu_route_target(raw)
    trimmed = parsed["target"].strip()
    if not trimmed:
        return None

    # Handle feishu: prefix (e.g. "feishu:ou_xxx" → "ou_xxx")
    if trimmed.startswith(TAG_FEISHU):
        inner = trimmed[len(TAG_FEISHU):].strip()
        if inner:
            return inner

    if trimmed.startswith(TAG_CHAT):
        return trimmed[len(TAG_CHAT):]
    if trimmed.startswith(TAG_USER):
        return trimmed[len(TAG_USER):]
    if trimmed.startswith(TAG_OPEN_ID):
        return trimmed[len(TAG_OPEN_ID):]

    return trimmed


def parse_feishu_route_target(raw: str) -> dict:
    """
    Parse a raw target string that may contain a #fragment with route metadata.

    Returns {"target": ..., "replyToMessageId"?: ..., "threadId"?: ...}
    """
    trimmed = raw.strip()
    if not trimmed:
        return {"target": ""}

    hash_index = trimmed.find("#")
    if hash_index < 0:
        return {"target": trimmed}

    target = trimmed[:hash_index].strip()
    fragment = trimmed[hash_index + 1:].strip()
    if not fragment:
        return {"target": target}

    result: dict = {"target": target}

    # Parse fragment as query-string
    try:
        from urllib.parse import parse_qs
        params = parse_qs(fragment)
        reply_to = params.get(ROUTE_META_FRAGMENT_REPLY_TO, [None])[0]
        if reply_to:
            result["replyToMessageId"] = normalize_message_id(reply_to.strip())
        thread_id = params.get(ROUTE_META_FRAGMENT_THREAD_ID, [None])[0]
        if thread_id:
            result["threadId"] = thread_id.strip()
    except Exception:
        pass

    return result


def encode_feishu_route_target(
    target: str,
    reply_to_message_id: str | None = None,
    thread_id: str | int | None = None,
) -> str:
    """Encode route metadata into a Feishu target string as a #fragment."""
    target = target.strip()
    if not target:
        return target

    reply_to = normalize_message_id(reply_to_message_id) if reply_to_message_id else None
    tid = str(thread_id).strip() if thread_id is not None and str(thread_id).strip() else None

    if not reply_to and not tid:
        return target

    from urllib.parse import urlencode
    params = {}
    if reply_to:
        params[ROUTE_META_FRAGMENT_REPLY_TO] = reply_to
    if tid:
        params[ROUTE_META_FRAGMENT_THREAD_ID] = tid

    return f"{target}#{urlencode(params)}"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_feishu_target(raw_id: str, id_type: FeishuIdType | None = None) -> str:
    """Add the appropriate routing prefix to a bare Feishu identifier."""
    resolved = id_type or detect_id_type(raw_id)
    if resolved == "chat_id":
        return f"{TAG_CHAT}{raw_id}"
    return f"{TAG_USER}{raw_id}"


# ---------------------------------------------------------------------------
# API receive-ID resolution
# ---------------------------------------------------------------------------


def resolve_receive_id_type(raw_id: str) -> str:
    """Determine receive_id_type query parameter for send-message API."""
    if raw_id.startswith(CHAT_PREFIX):
        return "chat_id"
    if raw_id.startswith(OPEN_ID_PREFIX):
        return "open_id"
    return "open_id"


# ---------------------------------------------------------------------------
# Message ID normalisation
# ---------------------------------------------------------------------------


def normalize_message_id(message_id: str | None) -> str | None:
    """
    Normalize message_id by stripping synthetic suffixes.

    Example: "om_xxx:auth-complete" → "om_xxx"
    """
    if not message_id:
        return None
    colon_index = message_id.find(":")
    if colon_index >= 0:
        return message_id[:colon_index]
    return message_id


# ---------------------------------------------------------------------------
# Quick predicate
# ---------------------------------------------------------------------------


def looks_like_feishu_id(raw: str) -> bool:
    """Return True when a raw string looks like a Feishu target."""
    if not raw:
        return False
    return (
        raw.startswith(TAG_CHAT)
        or raw.startswith(TAG_USER)
        or raw.startswith(TAG_OPEN_ID)
        or raw.startswith(CHAT_PREFIX)
        or raw.startswith(OPEN_ID_PREFIX)
    )
