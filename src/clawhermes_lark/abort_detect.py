"""
Abort trigger detection for fast-path streaming cancellation.

Aligns with larksuite/openclaw-lark src/channel/abort-detect.ts:
  - Trigger word list synced with OpenClaw core abort module
  - Multi-language support (en/zh/ja/hi/ar/ru/de/fr/es)
  - `/stop` command form detection
  - Conversation stop-intent phrase detection (superset)
  - Raw text extraction from Feishu message events
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Trigger word list (synced with OpenClaw core abort.ts)
# ---------------------------------------------------------------------------

ABORT_TRIGGERS: set[str] = {
    "stop",
    "esc",
    "abort",
    "wait",
    "exit",
    "interrupt",
    "detente",
    "deten",
    "detén",
    "arrete",
    "arrête",
    "停止",
    "やめて",
    "止めて",
    "रुको",
    "توقف",
    "стоп",
    "остановись",
    "останови",
    "остановить",
    "прекрати",
    "halt",
    "anhalten",
    "aufhören",
    "hoer auf",
    "stopp",
    "pare",
    "stop openclaw",
    "openclaw stop",
    "stop action",
    "stop current action",
    "stop run",
    "stop current run",
    "stop agent",
    "stop the agent",
    "stop don't do anything",
    "stop dont do anything",
    "stop do not do anything",
    "stop doing anything",
    "do not do that",
    "please stop",
    "stop please",
}

# ---------------------------------------------------------------------------
# Conversation stop-intent phrases (superset of ABORT_TRIGGERS)
# ---------------------------------------------------------------------------

STOP_INTENT_PHRASES: list[str] = [
    # zh — stop / terminate / pause
    "中断", "中止", "终止", "停止", "停下", "停一下", "暂停", "打住", "停手", "收手",
    # zh — "don't keep going / replying"
    "别聊", "别说了", "别回复", "别继续", "别再聊", "别再说",
    "别吵", "别争", "不要回复", "不要继续", "不用回复", "不用继续",
    # zh — "wrap up / be quiet"
    "结束对话", "结束讨论", "结束辩论", "到此为止", "闭嘴",
    # en
    "stop talking", "stop chatting", "stop debating",
    "stop the debate", "stop the conversation", "stop this conversation",
    "stop responding", "stop replying",
    "end the conversation", "end conversation", "end the debate",
    "shut up", "be quiet", "cut it out", "knock it off",
    "wrap it up", "stand down",
]

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_TRAILING_ABORT_PUNCTUATION_RE = re.compile(r"[.!?…,，。;；:：'\")\]}]+$")
_MENTION_RE = re.compile(r"@_user_\d+")

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalize_abort_trigger_text(text: str) -> str:
    """Normalize text for exact trigger-word matching."""
    return (
        text.strip()
        .lower()
        .replace("`", "'")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _strip_trailing_abort_punctuation(text: str) -> str:
    """Strip trailing punctuation for cleaner trigger matching."""
    return _TRAILING_ABORT_PUNCTUATION_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_abort_trigger(text: str) -> bool:
    """Exact trigger-word match (same logic as OpenClaw core `isAbortTrigger`)."""
    if not text:
        return False
    normalized = _normalize_abort_trigger_text(text)
    stripped = _strip_trailing_abort_punctuation(normalized)
    return stripped in ABORT_TRIGGERS


def is_likely_abort_text(text: str) -> bool:
    """
    Extended abort detection: matches bare trigger words and the
    `/stop` command form. Used by the monitor/adapter fast-path.
    """
    if not text:
        return False
    trimmed = text.strip().lower()
    if trimmed == "/stop":
        return True
    return is_abort_trigger(trimmed)


def is_conversation_stop_intent(text: str) -> bool:
    """
    Whether an inbound message expresses intent to stop / interrupt
    the ongoing exchange. Superset of is_likely_abort_text plus
    conversational phrases.
    """
    if not text:
        return False
    # Drop bot mention placeholders so "@Bot 中断对话" → "中断对话"
    normalized = _MENTION_RE.sub("", text).strip().lower()
    if not normalized:
        return False
    if is_likely_abort_text(normalized):
        return True
    return any(p in normalized for p in STOP_INTENT_PHRASES)


def extract_raw_text_from_event(message: Any) -> str | None:
    """
    Extract the raw text payload from a Feishu message event.

    Only handles `text` type messages. The `message.content` field is
    a JSON string like `{"text":"hello"}`.

    In group chats, bot mention placeholders (`@_user_N`) are stripped
    so a message like `@Bot stop` is detected as `stop`.
    """
    import json

    msg_event = getattr(message, "event", None) or message
    msg = getattr(msg_event, "message", None)
    if msg is None:
        return None

    msg_type = getattr(msg, "message_type", "")
    if msg_type != "text":
        return None

    content_str = getattr(msg, "content", "")
    if not content_str:
        return None

    try:
        parsed = json.loads(content_str) if isinstance(content_str, str) else content_str
    except (json.JSONDecodeError, TypeError):
        return None

    text = parsed.get("text", "") if isinstance(parsed, dict) else ""
    if not isinstance(text, str):
        return None

    # Strip bot mention placeholders
    text = _MENTION_RE.sub("", text).strip()
    return text or None
