"""
Streaming card controller — manages the full lifecycle of streaming card updates.

Aligns with larksuite/openclaw-lark src/card/streaming-card-controller.ts:
  - State machine: idle → creating → streaming → completed / aborted / terminated
  - Content accumulation with typewriter-style streaming
  - Reasoning text handling
  - Tool use integration
  - Flush throttling via FlushController
  - Graceful shutdown and abort
  - Message-unavailable detection

Used by the reply dispatcher for streaming-mode card replies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from clawhermes_lark.openclaw_lark.card.builder import (
    STREAMING_ELEMENT_ID,
    REASONING_ELEMENT_ID,
    build_complete_card,
    build_streaming_card,
    build_thinking_card,
    normalize_card_content,
    split_reasoning_text,
    to_cardkit_v2,
)
from clawhermes_lark.openclaw_lark.card.error import (
    is_card_error,
    is_card_rate_limit_error,
    sanitize_text_segments_for_card,
)
from clawhermes_lark.openclaw_lark.card.flush import FlushController
from clawhermes_lark.openclaw_lark.card.tool_use import (
    build_tool_use_summary,
    build_tool_use_title_suffix,
)

logger = logging.getLogger("clawhermes.lark.streaming_card")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THROTTLE_FLUSH_MS = 500       # Minimum interval between card content updates
THROTTLE_BACKOFF_MS = 2000    # Backoff after rate-limit error
MAX_CARD_CONTENT_LENGTH = 7_000

# Card phases
PHASE_IDLE = "idle"
PHASE_CREATING = "creating"
PHASE_STREAMING = "streaming"
PHASE_COMPLETED = "completed"
PHASE_ABORTED = "aborted"
PHASE_TERMINATED = "terminated"

TERMINAL_PHASES = {PHASE_COMPLETED, PHASE_ABORTED, PHASE_TERMINATED}

# Empty reply fallback
EMPTY_REPLY_FALLBACK_TEXT = "*(空响应)*"


# ---------------------------------------------------------------------------
# StreamingCardController
# ---------------------------------------------------------------------------


class StreamingCardController:
    """
    Manages the full lifecycle of a streaming Feishu card.

    State machine:
      idle → creating → streaming → completed | aborted | terminated
    """

    __slots__ = (
        "_cfg",
        "_agent_id",
        "_session_key",
        "_account_id",
        "_chat_id",
        "_reply_to_message_id",
        "_reply_in_thread",
        "_tool_use_display",
        "_footer_config",
        "_phase",
        "_card_message_id",
        "_card_sequence",
        "_accumulated_text",
        "_completed_text",
        "_last_flushed_text",
        "_reasoning_text",
        "_tool_use_steps",
        "_flush_controller",
        "_flush_task",
        "_abort_controller",
        "_terminated",
        "_running",
        "_create_card_fn",
        "_update_card_fn",
    )

    def __init__(
        self,
        cfg: dict[str, Any],
        agent_id: str = "",
        session_key: str = "",
        account_id: str = "",
        chat_id: str = "",
        reply_to_message_id: str = "",
        reply_in_thread: bool = False,
        tool_use_display: bool = True,
        footer_config: dict[str, bool] | None = None,
        create_card_fn: Callable | None = None,
        update_card_fn: Callable | None = None,
    ):
        self._cfg = cfg
        self._agent_id = agent_id
        self._session_key = session_key
        self._account_id = account_id
        self._chat_id = chat_id
        self._reply_to_message_id = reply_to_message_id
        self._reply_in_thread = reply_in_thread
        self._tool_use_display = tool_use_display
        self._footer_config = footer_config or {}

        # State machine
        self._phase = PHASE_IDLE

        # Card state
        self._card_message_id: str | None = None
        self._card_sequence = 0

        # Text accumulation
        self._accumulated_text = ""
        self._completed_text = ""
        self._last_flushed_text = ""

        # Reasoning
        self._reasoning_text: str | None = None

        # Tool use
        self._tool_use_steps: list[dict] = []

        # Flush control
        self._flush_controller = FlushController(interval_ms=THROTTLE_FLUSH_MS)
        self._flush_task: asyncio.Task | None = None

        # Abort / termination
        self._abort_controller: asyncio.Task | None = None
        self._terminated = False
        self._running = True

        # External send functions
        self._create_card_fn = create_card_fn
        self._update_card_fn = update_card_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def card_message_id(self) -> str | None:
        return self._card_message_id

    @property
    def is_terminated(self) -> bool:
        return self._phase in TERMINAL_PHASES

    @property
    def flush_controller(self) -> FlushController:
        return self._flush_controller

    def set_send_functions(
        self,
        create_card_fn: Callable,
        update_card_fn: Callable,
    ) -> None:
        """Inject the external card send/update functions."""
        self._create_card_fn = create_card_fn
        self._update_card_fn = update_card_fn

    # ------------------------------------------------------------------
    # Card lifecycle
    # ------------------------------------------------------------------

    async def start_thinking(self) -> None:
        """Create the initial 'thinking' card."""
        if self._phase != PHASE_IDLE or not self._create_card_fn:
            return

        self._phase = PHASE_CREATING

        card = build_thinking_card()
        v2_card = to_cardkit_v2(card)
        v2_card["card"] = card  # Keep original for later updates

        try:
            result = await self._create_card_fn(
                chat_id=self._chat_id,
                card=v2_card,
                reply_to_message_id=self._reply_to_message_id,
                reply_in_thread=self._reply_in_thread,
            )
            if result and result.get("message_id"):
                self._card_message_id = result["message_id"]
                self._phase = PHASE_STREAMING
                logger.debug("Thinking card created: msg_id=%s", self._card_message_id)
        except Exception:
            logger.warning("Failed to create thinking card", exc_info=True)
            self._phase = PHASE_TERMINATED

    async def append_text(self, text: str) -> None:
        """Append streaming text to the card content."""
        if self._phase != PHASE_STREAMING:
            return

        self._accumulated_text += text
        await self._flush_content()

    async def complete(
        self,
        final_text: str | None = None,
        reasoning_text: str | None = None,
    ) -> None:
        """Complete the streaming card with final content."""
        if self._phase in TERMINAL_PHASES:
            return

        # Use accumulated text or provided final text
        content = final_text or self._accumulated_text
        if not content:
            content = EMPTY_REPLY_FALLBACK_TEXT

        self._completed_text = content
        self._reasoning_text = reasoning_text or self._reasoning_text

        if self._phase == PHASE_STREAMING and self._update_card_fn:
            await self._flush_final()
        elif self._phase in (PHASE_IDLE, PHASE_CREATING) and self._create_card_fn:
            # Never started streaming — create the card directly
            await self._create_final_card(content)

        self._phase = PHASE_COMPLETED
        self._running = False

    async def abort(self, reason: str = "aborted") -> None:
        """Abort the streaming card (e.g., on user cancel)."""
        if self._phase in TERMINAL_PHASES:
            return

        self._phase = PHASE_ABORTED
        self._running = False
        logger.debug("Streaming card aborted: %s", reason)

    async def terminate(self, reason: str = "terminated") -> None:
        """Force-terminate the streaming card."""
        if self._phase in TERMINAL_PHASES:
            return

        self._phase = PHASE_TERMINATED
        self._running = False
        logger.debug("Streaming card terminated: %s", reason)

    # ------------------------------------------------------------------
    # Tool use
    # ------------------------------------------------------------------

    def add_tool_step(self, step: dict[str, Any]) -> None:
        """Record a tool use step for display in the card."""
        if not self._tool_use_display:
            return
        self._tool_use_steps.append(step)

    def update_tool_step(self, index: int, update: dict[str, Any]) -> None:
        """Update the status/result of a previously recorded tool step."""
        if 0 <= index < len(self._tool_use_steps):
            self._tool_use_steps[index].update(update)

    # ------------------------------------------------------------------
    # Card abort fast-path
    # ------------------------------------------------------------------

    async def abort_card(self) -> None:
        """Called externally to abort the current card from fast-path."""
        await self.abort("fast_abort")

    # ------------------------------------------------------------------
    # Internal — flush
    # ------------------------------------------------------------------

    async def _flush_content(self) -> None:
        """Flush accumulated streaming content to the card."""
        if not self._update_card_fn or not self._card_message_id:
            return

        text = normalize_card_content(self._accumulated_text, MAX_CARD_CONTENT_LENGTH)
        if text == self._last_flushed_text:
            return  # No change, skip

        self._last_flushed_text = text

        # Build tool use content
        tool_content = ""
        if self._tool_use_display and self._tool_use_steps:
            tool_content = build_tool_use_summary(self._tool_use_steps)

        # Split reasoning from accumulated text
        split = split_reasoning_text(text)
        reasoning = split.get("reasoningText") or self._reasoning_text
        answer = split.get("answerText", text)

        card = build_streaming_card(
            text=answer,
            reasoning_text=reasoning,
            tool_use_content=tool_content,
        )
        v2_card = to_cardkit_v2(card)

        # Throttle: enqueue to FlushController instead of sending directly.
        # Only the latest payload matters — the controller will send it at
        # a controlled interval, coalescing rapid updates into fewer API calls.
        await self._flush_controller.enqueue(v2_card)

        # Attempt a flush now; FlushController respects the interval internally.
        async def _sender(payload):
            try:
                await self._update_card_fn(
                    message_id=self._card_message_id,
                    card=payload,
                )
            except Exception as e:
                await self._handle_flush_error(e)

        await self._flush_controller.flush(_sender)

    async def _flush_final(self) -> None:
        """Flush the final completed state to the card."""
        if not self._update_card_fn or not self._card_message_id:
            return

        text = normalize_card_content(self._completed_text, MAX_CARD_CONTENT_LENGTH)
        self._last_flushed_text = text

        tool_content = ""
        if self._tool_use_display and self._tool_use_steps:
            tool_content = build_tool_use_summary(self._tool_use_steps)

        # Build footer
        footer_text = self._build_footer()

        card = build_complete_card(
            text=text,
            reasoning_text=self._reasoning_text,
            tool_use_content=tool_content,
            footer_text=footer_text,
        )
        v2_card = to_cardkit_v2(card)

        # Final flush — discard any pending throttled updates and send directly
        self._flush_controller.clear()
        try:
            await self._update_card_fn(
                message_id=self._card_message_id,
                card=v2_card,
            )
        except Exception as e:
            await self._handle_flush_error(e)

    async def _create_final_card(self, content: str) -> None:
        """Create the card directly in completed state (no streaming phase)."""
        if not self._create_card_fn:
            return

        text = normalize_card_content(content, MAX_CARD_CONTENT_LENGTH)
        footer_text = self._build_footer()

        card = build_complete_card(
            text=text,
            reasoning_text=self._reasoning_text,
            footer_text=footer_text,
        )
        v2_card = to_cardkit_v2(card)
        v2_card["card"] = card

        try:
            result = await self._create_card_fn(
                chat_id=self._chat_id,
                card=v2_card,
                reply_to_message_id=self._reply_to_message_id,
                reply_in_thread=self._reply_in_thread,
            )
            if result and result.get("message_id"):
                self._card_message_id = result["message_id"]
        except Exception:
            logger.warning("Failed to create final card", exc_info=True)

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _build_footer(self) -> str | None:
        """Build card footer text based on configuration."""
        parts: list[str] = []
        config = self._footer_config

        if config.get("status"):
            phase_labels = {
                PHASE_STREAMING: "处理中",
                PHASE_COMPLETED: "已完成",
                PHASE_ABORTED: "已取消",
                PHASE_TERMINATED: "已终止",
            }
            label = phase_labels.get(self._phase, "")
            if label:
                parts.append(label)

        if parts:
            return " · ".join(parts)
        return None

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    async def _handle_flush_error(self, error: Exception) -> None:
        """Handle card update errors (rate limits, etc.)."""
        logger.debug("Card flush error: %s", error)
        # Check if it's a rate-limit error
        if hasattr(error, "code"):
            code = getattr(error, "code", 0)
            if is_card_rate_limit_error(code):
                # Apply backoff
                await asyncio.sleep(THROTTLE_BACKOFF_MS / 1000)
                return
            if is_card_error(code):
                logger.warning("Card error code=%s, stopping stream", code)
                await self.terminate(f"card_error_{code}")
                return

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def dispose(self) -> None:
        """Clean up all resources."""
        self._running = False
        self._flush_controller.clear()
        if self._abort_controller and not self._abort_controller.done():
            self._abort_controller.cancel()
