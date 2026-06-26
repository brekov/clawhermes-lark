"""
Reply dispatcher factory — creates the appropriate reply strategy for Feishu.

Aligns with larksuite/openclaw-lark src/card/reply-dispatcher.ts:
  - Resolves reply mode (streaming card vs static text)
  - Streaming mode delegates to StreamingCardController
  - Static mode delivers via send_message
  - Typing indicator management (reaction-based)
  - Footer configuration resolution
  - Block streaming support

Entry point for agent → channel reply dispatch.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from clawhermes_lark.openclaw.streaming_card import StreamingCardController

logger = logging.getLogger("clawhermes.lark.reply_dispatcher")

# ---------------------------------------------------------------------------
# Reply mode
# ---------------------------------------------------------------------------

ReplyMode = str  # "streaming" | "static" | "auto" | "off"


def resolve_reply_mode(
    feishu_cfg: dict[str, Any] | None,
    chat_type: str = "p2p",
) -> ReplyMode:
    """
    Resolve the effective reply mode from channel config.

    Priority:
      1. feishu_cfg.replyMode (explicit setting)
      2. Default: "auto" → resolves to "streaming" for groups, "static" for p2p
    """
    if feishu_cfg is None:
        return "auto"

    mode = feishu_cfg.get("replyMode") or feishu_cfg.get("reply_mode") or "auto"

    if mode == "auto":
        # Auto: streaming cards for groups, static for direct messages
        return "streaming" if chat_type == "group" else "static"

    if mode in ("streaming", "static", "off"):
        return mode

    return "auto"


def resolve_footer_config(raw_footer: dict[str, bool] | None) -> dict[str, bool]:
    """
    Resolve card footer visibility configuration.

    Defaults: status=True, elapsed=False, tokens=False, cache=False,
              context=False, model=False
    """
    defaults: dict[str, bool] = {
        "status": True,
        "elapsed": False,
        "tokens": False,
        "cache": False,
        "context": False,
        "model": False,
    }
    if raw_footer:
        defaults.update(raw_footer)
    return defaults


# ---------------------------------------------------------------------------
# Typing Indicator (simulated via reactions)
# ---------------------------------------------------------------------------


class TypingIndicatorManager:
    """
    Manages typing indicator state for a conversation.

    Feishu doesn't have a native typing indicator API; this simulates
    it by adding/removing a CLOCK reaction on the user's message.
    """

    def __init__(self, client: Any, message_id: str):
        self._client = client
        self._message_id = message_id
        self._reaction_id: str | None = None
        self._active = False

    async def start(self) -> None:
        """Show typing indicator."""
        if self._active:
            return
        try:
            from clawhermes_lark.openclaw.messaging import add_reaction
            result = await add_reaction(
                self._client, self._message_id, "CLOCK"
            )
            if result:
                self._reaction_id = result.reaction_id
            self._active = True
        except Exception:
            logger.debug("Failed to start typing indicator", exc_info=True)

    async def stop(self) -> None:
        """Hide typing indicator."""
        if not self._active:
            return
        try:
            if self._reaction_id:
                from clawhermes_lark.openclaw.messaging import remove_reaction
                await remove_reaction(
                    self._client, self._message_id, self._reaction_id
                )
            self._active = False
            self._reaction_id = None
        except Exception:
            logger.debug("Failed to stop typing indicator", exc_info=True)

    async def swap_error(self) -> None:
        """Swap typing indicator to error (cross mark)."""
        await self.stop()
        try:
            from clawhermes_lark.openclaw.messaging import add_reaction
            await add_reaction(self._client, self._message_id, "CROSS_MARK")
        except Exception:
            logger.debug("Failed to swap to error reaction", exc_info=True)

    def dispose(self) -> None:
        """Clean up the indicator."""
        if self._active:
            asyncio.ensure_future(self.stop())


# ---------------------------------------------------------------------------
# Reply Dispatcher
# ---------------------------------------------------------------------------


class ReplyDispatcher:
    """
    Manages the reply workflow for a single agent → channel dispatch.

    Supports two modes:
      - streaming: uses StreamingCardController with live-updating cards
      - static: delivers as a single text message (or card)
    """

    __slots__ = (
        "_cfg",
        "_agent_id",
        "_chat_id",
        "_session_key",
        "_reply_to_message_id",
        "_account_id",
        "_reply_in_thread",
        "_thread_id",
        "_chat_type",
        "_streaming_controller",
        "_typing_indicator",
        "_send_message_fn",
        "_send_card_fn",
        "_update_card_fn",
        "_use_streaming",
        "_footer_config",
    )

    def __init__(
        self,
        cfg: dict[str, Any],
        agent_id: str = "",
        chat_id: str = "",
        session_key: str = "",
        reply_to_message_id: str = "",
        account_id: str = "",
        reply_in_thread: bool = False,
        thread_id: str | None = None,
        chat_type: str = "p2p",
        feishu_cfg: dict[str, Any] | None = None,
        send_message_fn: Callable | None = None,
        send_card_fn: Callable | None = None,
        update_card_fn: Callable | None = None,
        typing_indicator: TypingIndicatorManager | None = None,
    ):
        self._cfg = cfg
        self._agent_id = agent_id
        self._chat_id = chat_id
        self._session_key = session_key
        self._reply_to_message_id = reply_to_message_id
        self._account_id = account_id
        self._reply_in_thread = reply_in_thread
        self._thread_id = thread_id
        self._chat_type = chat_type
        self._send_message_fn = send_message_fn
        self._send_card_fn = send_card_fn
        self._update_card_fn = update_card_fn
        self._typing_indicator = typing_indicator

        # Resolve footer config
        raw_footer = (feishu_cfg or {}).get("footer")
        self._footer_config = resolve_footer_config(raw_footer)

        # Resolve reply mode
        mode = resolve_reply_mode(feishu_cfg, chat_type)
        self._use_streaming = mode == "streaming"

        # Create streaming controller if needed
        self._streaming_controller: StreamingCardController | None = None
        if self._use_streaming:
            self._streaming_controller = StreamingCardController(
                cfg=cfg,
                agent_id=agent_id,
                session_key=session_key,
                account_id=account_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                reply_in_thread=reply_in_thread,
                footer_config=self._footer_config,
                create_card_fn=self._send_card_fn,
                update_card_fn=self._update_card_fn,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_streaming(self) -> bool:
        return self._use_streaming

    @property
    def streaming_controller(self) -> StreamingCardController | None:
        return self._streaming_controller

    async def start_thinking(self) -> None:
        """Show typing indicator and (if streaming) create thinking card."""
        if self._typing_indicator:
            await self._typing_indicator.start()

        if self._streaming_controller:
            await self._streaming_controller.start_thinking()

    async def append_text(self, text: str) -> None:
        """Append streaming text chunk."""
        if self._streaming_controller:
            await self._streaming_controller.append_text(text)

    async def complete(self, final_text: str) -> None:
        """Complete the reply.

        In streaming mode: updates the card to 'complete' state.
        In static mode: sends the final text as a message.
        """
        # Stop typing indicator
        if self._typing_indicator:
            await self._typing_indicator.stop()

        if self._streaming_controller:
            await self._streaming_controller.complete(final_text)
        elif self._send_message_fn:
            try:
                await self._send_message_fn(
                    chat_id=self._chat_id,
                    text=final_text,
                    reply_to_message_id=self._reply_to_message_id,
                    reply_in_thread=self._reply_in_thread,
                )
            except Exception:
                logger.exception("Static send failed")

    async def error(self, error_text: str) -> None:
        """Handle reply error — swap typing indicator to error."""
        if self._typing_indicator:
            await self._typing_indicator.swap_error()

        if self._streaming_controller:
            await self._streaming_controller.terminate(f"error: {error_text}")

    async def abort(self, reason: str = "user_abort") -> None:
        """Abort the reply."""
        if self._typing_indicator:
            await self._typing_indicator.stop()

        if self._streaming_controller:
            await self._streaming_controller.abort(reason)

    def add_tool_step(self, step: dict[str, Any]) -> None:
        """Record a tool use step."""
        if self._streaming_controller:
            self._streaming_controller.add_tool_step(step)

    def dispose(self) -> None:
        """Clean up all resources."""
        if self._typing_indicator:
            self._typing_indicator.dispose()
        if self._streaming_controller:
            self._streaming_controller.dispose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_reply_dispatcher(
    cfg: dict[str, Any],
    agent_id: str = "",
    chat_id: str = "",
    session_key: str = "",
    reply_to_message_id: str = "",
    account_id: str = "",
    reply_in_thread: bool = False,
    thread_id: str | None = None,
    chat_type: str = "p2p",
    feishu_cfg: dict[str, Any] | None = None,
    send_message_fn: Callable | None = None,
    send_card_fn: Callable | None = None,
    update_card_fn: Callable | None = None,
    typing_indicator: TypingIndicatorManager | None = None,
) -> ReplyDispatcher:
    """Create a reply dispatcher for an agent → channel reply."""
    return ReplyDispatcher(
        cfg=cfg,
        agent_id=agent_id,
        chat_id=chat_id,
        session_key=session_key,
        reply_to_message_id=reply_to_message_id,
        account_id=account_id,
        reply_in_thread=reply_in_thread,
        thread_id=thread_id,
        chat_type=chat_type,
        feishu_cfg=feishu_cfg,
        send_message_fn=send_message_fn,
        send_card_fn=send_card_fn,
        update_card_fn=update_card_fn,
        typing_indicator=typing_indicator,
    )
