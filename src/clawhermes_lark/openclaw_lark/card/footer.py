"""
Card footer configuration for Feishu interactive cards.

Aligns with larksuite/openclaw-lark src/core/footer-config.ts:
  - Per-feature visibility toggles for card footer metadata
  - Defaults: status=True, elapsed=False, tokens=False, etc.
  - Build footer text from resolved config

Controls what metadata appears in the footer of completed cards.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_FOOTER_CONFIG: dict[str, bool] = {
    "status": True,
    "elapsed": False,
    "tokens": False,
    "cache": False,
    "context": False,
    "model": False,
}


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def resolve_footer_config(
    raw: dict[str, bool] | None,
) -> dict[str, bool]:
    """
    Resolve footer visibility config, filling defaults for unset keys.
    """
    result = dict(DEFAULT_FOOTER_CONFIG)
    if raw:
        # Only accept known keys
        for key in DEFAULT_FOOTER_CONFIG:
            if key in raw:
                result[key] = bool(raw[key])
    return result


# ---------------------------------------------------------------------------
# Footer text builder
# ---------------------------------------------------------------------------


def build_footer_text(
    footer_config: dict[str, bool],
    status: str = "",
    elapsed_ms: int | None = None,
    tokens_used: int | None = None,
    model_name: str = "",
    context_used: int | None = None,
    cache_hit: bool = False,
) -> str | None:
    """
    Build card footer text from resolved config and available metadata.

    Returns a human-readable footer string, or None if nothing to show.
    """
    parts: list[str] = []

    if footer_config.get("status") and status:
        parts.append(status)

    if footer_config.get("elapsed") and elapsed_ms is not None:
        seconds = elapsed_ms / 1000
        if seconds >= 60:
            parts.append(f"耗时 {seconds / 60:.1f}min")
        else:
            parts.append(f"耗时 {seconds:.1f}s")

    if footer_config.get("tokens") and tokens_used is not None:
        if tokens_used >= 1000:
            parts.append(f"{tokens_used / 1000:.1f}k tokens")
        else:
            parts.append(f"{tokens_used} tokens")

    if footer_config.get("model") and model_name:
        parts.append(model_name)

    if footer_config.get("context") and context_used is not None:
        parts.append(f"上下文 {context_used}")

    if footer_config.get("cache") and cache_hit:
        parts.append("缓存命中")

    if not parts:
        return None

    return " · ".join(parts)
