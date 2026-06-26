"""
Interactive card action dispatch for Feishu card.action.trigger events.

Aligns with larksuite/openclaw-lark src/channel/interactive-dispatch.ts:
  - Extract action basics from card callback events
  - Build respond helpers (reply, followUp, editMessage)
  - Dispatch to registered interactive handlers by namespace
  - Deduplicate via per-event hashing
  - Return card responses (toast, updated card)

Handles the Feishu-specific card action → handler pipeline.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from typing import Any, Callable

from clawhermes_lark.openclaw_lark.card.builder import (
    build_markdown_element,
    build_card,
    to_cardkit_v2,
)

logger = logging.getLogger("clawhermes.lark.interactive")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEDUP_CACHE_SIZE = 256
MAX_RESPOND_TEXT_LENGTH = 7_000


# ---------------------------------------------------------------------------
# Interactive handler types
# ---------------------------------------------------------------------------


class InteractiveHandler:
    """
    A registered handler for a specific interactive card action namespace.

    Usage:
      handler = InteractiveHandler(namespace="my_plugin")
      handler.on_action("confirm", my_handler_fn)
      dispatcher.register(handler)
    """

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._actions: dict[str, Callable] = {}

    def on_action(self, action: str, handler_fn: Callable) -> None:
        """Register a handler for a specific action value."""
        self._actions[action] = handler_fn

    async def handle(self, ctx: "InteractiveContext") -> Any:
        """Dispatch to the matching action handler."""
        handler_fn = self._actions.get(ctx.action)
        if handler_fn:
            return await handler_fn(ctx)
        return None


class InteractiveContext:
    """Context object passed to interactive card action handlers."""

    __slots__ = (
        "channel", "account_id", "sender_id", "conversation_id",
        "message_id", "namespace", "payload", "action",
        "raw_event", "respond",
    )

    def __init__(
        self,
        channel: str = "feishu",
        account_id: str = "",
        sender_id: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        namespace: str = "",
        payload: str = "",
        action: str = "",
        raw_event: Any = None,
        respond: "InteractiveRespond" | None = None,
    ):
        self.channel = channel
        self.account_id = account_id
        self.sender_id = sender_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.namespace = namespace
        self.payload = payload
        self.action = action
        self.raw_event = raw_event
        self.respond = respond or InteractiveRespond()


class InteractiveRespond:
    """Respond helpers for interactive card handlers."""

    def __init__(
        self,
        send_message_fn: Callable | None = None,
        update_card_fn: Callable | None = None,
        send_card_fn: Callable | None = None,
        chat_id: str = "",
        message_id: str = "",
        account_id: str = "",
    ):
        self._send_message = send_message_fn
        self._update_card = update_card_fn
        self._send_card = send_card_fn
        self._chat_id = chat_id
        self._message_id = message_id
        self._account_id = account_id

    async def reply(self, text: str) -> None:
        """Reply with a text message in the same conversation."""
        if not self._chat_id or not text.strip():
            return
        if self._send_message:
            await self._send_message(
                to=self._chat_id,
                text=text[:MAX_RESPOND_TEXT_LENGTH],
                reply_to_message_id=self._message_id,
                account_id=self._account_id,
            )

    async def follow_up(self, text: str) -> None:
        """Send a follow-up message (same as reply for Feishu)."""
        await self.reply(text)

    async def edit_message(self, text: str = "", blocks: list[dict] | None = None) -> None:
        """Edit the current message or update the card."""
        if self._update_card and self._message_id:
            if blocks:
                card = {"schema": "2.0", "body": {"elements": blocks}}
                await self._update_card(
                    message_id=self._message_id,
                    card=card,
                )
            elif text.strip():
                card = build_card(
                    elements=[build_markdown_element(text[:MAX_RESPOND_TEXT_LENGTH])],
                )
                v2 = to_cardkit_v2(card)
                await self._update_card(
                    message_id=self._message_id,
                    card=v2,
                )
            else:
                # Send empty update to clear
                card = {"schema": "2.0", "body": {"elements": []}}
                await self._update_card(
                    message_id=self._message_id,
                    card=card,
                )
        elif self._send_card and self._chat_id:
            if blocks:
                card = {"schema": "2.0", "body": {"elements": blocks}}
                await self._send_card(
                    to=self._chat_id,
                    card=card,
                    reply_to_message_id=self._message_id,
                    account_id=self._account_id,
                )
            elif text.strip():
                # Send as new card message
                await self._send_card(
                    to=self._chat_id,
                    card=build_card(
                        elements=[build_markdown_element(text[:MAX_RESPOND_TEXT_LENGTH])],
                    ),
                    reply_to_message_id=self._message_id,
                    account_id=self._account_id,
                )

    async def toast(self, content: str, toast_type: str = "info") -> dict:
        """Return a toast response (for synchronous card action handlers)."""
        return {"toast": {"type": toast_type, "content": content}}


# ---------------------------------------------------------------------------
# Interactive dispatcher
# ---------------------------------------------------------------------------


class InteractiveDispatcher:
    """
    Central dispatcher for Feishu card action events.

    Routes card.action.trigger callbacks to registered InteractiveHandlers.
    """

    def __init__(self):
        self._handlers: dict[str, InteractiveHandler] = {}
        self._dedup: "OrderedDict[str, float]" = OrderedDict()
        self._dedup_max = DEDUP_CACHE_SIZE

    def register(self, handler: InteractiveHandler) -> None:
        """Register an interactive handler by namespace."""
        self._handlers[handler.namespace] = handler
        logger.debug("Registered interactive handler: %s", handler.namespace)

    def unregister(self, namespace: str) -> None:
        """Remove a registered handler."""
        self._handlers.pop(namespace, None)

    def _extract_basics(self, data: Any) -> dict[str, Any] | None:
        """
        Extract basic fields from a card.action.trigger event payload.

        Returns:
          dict with action, senderOpenId, openChatId, openMessageId
          or None if the event doesn't look like an interactive action.
        """
        try:
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return None

            # Extract action value
            action_val = None
            action_obj = data.get("action", {})
            if isinstance(action_obj, dict):
                value = action_obj.get("value", {})
                if isinstance(value, dict):
                    action_val = value.get("action")
                elif isinstance(value, str):
                    action_val = value
            if not action_val or not isinstance(action_val, str):
                return None

            # Extract context fields
            open_chat_id = data.get("open_chat_id") or (
                data.get("context", {}).get("open_chat_id")
                if isinstance(data.get("context"), dict) else None
            )
            open_message_id = data.get("open_message_id") or (
                data.get("context", {}).get("open_message_id")
                if isinstance(data.get("context"), dict) else None
            )

            # Extract sender
            operator = data.get("operator", {})
            sender_id = None
            if isinstance(operator, dict):
                sender_id = operator.get("open_id") or operator.get("user_id")

            return {
                "action": action_val.strip(),
                "senderOpenId": sender_id,
                "openChatId": open_chat_id,
                "openMessageId": open_message_id,
            }
        except Exception:
            logger.debug("Failed to extract interactive basics", exc_info=True)
            return None

    def _dedup_check(self, key: str) -> bool:
        """Check if this event is a duplicate. Returns True if new."""
        import time
        if key in self._dedup:
            return False
        while len(self._dedup) >= self._dedup_max:
            self._dedup.popitem(last=False)
        self._dedup[key] = time.time()
        return True

    async def dispatch(
        self,
        data: Any,
        account_id: str = "",
        send_message_fn: Callable | None = None,
        update_card_fn: Callable | None = None,
        send_card_fn: Callable | None = None,
    ) -> Any:
        """
        Dispatch a card.action.trigger event to registered handlers.

        Returns:
          Handler response (toast dict, updated card, etc.) or None.
        """
        basics = self._extract_basics(data)
        if not basics:
            return None

        action = basics["action"]

        # Deduplicate
        dedup_key = (
            f"{account_id}:{basics.get('openChatId', '-')}:"
            f"{basics.get('openMessageId', '-')}:"
            f"{basics.get('senderOpenId', '-')}:{action}"
        )
        if not self._dedup_check(dedup_key):
            logger.debug("Duplicate card action ignored: %s", dedup_key)
            return None

        # Find matching handler — action format: "namespace:action_name"
        # or just "action_name" (no namespace → try all handlers)
        if ":" in action:
            namespace, action_name = action.split(":", 1)
            handler = self._handlers.get(namespace)
            if handler and handler._actions.get(action_name):
                pass  # handler found
            else:
                return None
        else:
            # Try all handlers
            handler = None
            for h in self._handlers.values():
                if action in h._actions:
                    handler = h
                    break
            if handler is None:
                return None

        # Build context
        respond = InteractiveRespond(
            send_message_fn=send_message_fn,
            update_card_fn=update_card_fn,
            send_card_fn=send_card_fn,
            chat_id=basics.get("openChatId", ""),
            message_id=basics.get("openMessageId", ""),
            account_id=account_id,
        )

        ctx = InteractiveContext(
            channel="feishu",
            account_id=account_id,
            sender_id=basics.get("senderOpenId"),
            conversation_id=basics.get("openChatId"),
            message_id=basics.get("openMessageId"),
            namespace=handler.namespace,
            payload=action,
            action=action.split(":", 1)[-1] if ":" in action else action,
            raw_event=data,
            respond=respond,
        )

        try:
            result = await handler.handle(ctx)
            return result
        except Exception:
            logger.exception("Interactive handler error for action=%s", action)
            return {
                "toast": {
                    "type": "error",
                    "content": "交互处理失败，请稍后重试",
                }
            }

    def get_handler_count(self) -> int:
        """Return the number of registered handlers."""
        return len(self._handlers)


# ---------------------------------------------------------------------------
# Singleton (process-level)
# ---------------------------------------------------------------------------

_default_dispatcher: InteractiveDispatcher | None = None


def get_interactive_dispatcher() -> InteractiveDispatcher:
    """Get or create the process-level interactive dispatcher singleton."""
    global _default_dispatcher
    if _default_dispatcher is None:
        _default_dispatcher = InteractiveDispatcher()
    return _default_dispatcher
