"""
Card error detection and handling for Feishu interactive cards.

Aligns with larksuite/openclaw-lark src/card/card-error.ts:
  - Detect card rate-limit errors (code 22801)
  - Detect card table-limit errors (code 17410)
  - Detect post-format errors (code 230001)
  - Sanitize text segments to avoid card rendering failures
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger("clawhermes.lark.card_error")

# ---------------------------------------------------------------------------
# Error code constants
# ---------------------------------------------------------------------------

# Card update rate limit — too many card updates in a short period
CARD_RATE_LIMIT_CODE = 22801

# Card table size/content exceeds limits
CARD_TABLE_LIMIT_CODE = 17410

# Post format error (occurs when content causes JSON parse failure)
POST_FORMAT_ERROR_PATTERN = re.compile(
    r"content format of the post type is incorrect", re.IGNORECASE
)

# Maximum number of text segments in a card element
FEISHU_CARD_TABLE_LIMIT = 500

# Max text segment length (characters)
MAX_TEXT_SEGMENT_LENGTH = 7_500

# Max elements per card
MAX_CARD_ELEMENTS = 50


# ---------------------------------------------------------------------------
# Error detection
# ---------------------------------------------------------------------------


def is_card_rate_limit_error(code: int) -> bool:
    """Check if an API error code indicates a card update rate limit."""
    return code == CARD_RATE_LIMIT_CODE


def is_card_table_limit_error(code: int) -> bool:
    """Check if an API error code indicates a card table/content limit."""
    return code == CARD_TABLE_LIMIT_CODE


def is_post_format_error(msg: str) -> bool:
    """Check if an error message indicates a post format error."""
    return bool(POST_FORMAT_ERROR_PATTERN.search(msg))


def is_card_error(code: int) -> bool:
    """Check if any card-related error was returned by the API."""
    return is_card_rate_limit_error(code) or is_card_table_limit_error(code)


# ---------------------------------------------------------------------------
# Text segment sanitization
# ---------------------------------------------------------------------------


def sanitize_text_segments_for_card(
    segments: list[dict],
    max_segments: int = FEISHU_CARD_TABLE_LIMIT,
    max_length: int = MAX_TEXT_SEGMENT_LENGTH,
) -> list[dict]:
    """
    Sanitize a list of card text segments to avoid rendering failures.

    - Truncates text segments that exceed the maximum length
    - Truncates the total segment count to the max allowed
    """
    if not segments:
        return segments

    sanitized: list[dict] = []
    for seg in segments[:max_segments]:
        text = seg.get("text", "")
        if isinstance(text, str) and len(text) > max_length:
            seg = {**seg, "text": text[:max_length] + "…"}
        sanitized.append(seg)

    return sanitized


def sanitize_card_content(text: str, max_length: int = MAX_TEXT_SEGMENT_LENGTH) -> str:
    """Truncate card text content to a safe length."""
    if not text:
        return text
    if len(text) > max_length:
        return text[:max_length] + "…"
    return text


def sanitize_elements_count(
    elements: list[dict], max_elements: int = MAX_CARD_ELEMENTS
) -> list[dict]:
    """Truncate card elements to the maximum allowed count."""
    if len(elements) > max_elements:
        logger.warning(
            "Card has %d elements, truncating to %d", len(elements), max_elements
        )
        return elements[:max_elements]
    return elements
