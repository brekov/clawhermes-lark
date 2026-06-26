"""
ClawHermes-Lark — 飞书/Lark 渠道适配器
基于 lark-oapi 官方 SDK，完整实现 larksuite/openclaw-lark 核心功能

独立 pip 包：pip install clawhermes-lark

层级架构：
  Layer 1: lark-oapi SDK — Token 管理、认证、API 调用
  Layer 2: ClawHermes-Lark 增强模块 — 去重、队列、中止、目标规范化
  Layer 3: 卡片系统 — Card Builder / Streaming / Reply Dispatcher
  Layer 4: OAPI 工具 — Sheets, Calendar, Drive, Wiki, Docs, IM, Search
  Layer 5: Onboarding — 配对引导 + OAuth 预授权
"""
from clawhermes_lark.adapter import (
    LarkAdapter,
    LarkConfig,
    LarkEventType,
    create_lark_adapter,
)
from clawhermes_lark.client import BotIdentity, LarkClient

from clawhermes_lark.messaging import (
    FeishuReaction,
    TypingIndicator,
    add_reaction,
    remove_reaction,
    list_reactions,
    edit_message,
    build_markdown_card,
    send_card,
)

# ── 增强模块 ───────────────────────────────────────────────────────
from clawhermes_lark.chat_queue import (
    build_queue_key,
    enqueue_feishu_chat_task,
    get_active_dispatcher,
    has_active_task,
    register_active_dispatcher,
    unregister_active_dispatcher,
)
from clawhermes_lark.dedup import MessageDedup, is_message_expired, create_message_dedup
from clawhermes_lark.abort_detect import (
    is_abort_trigger,
    is_likely_abort_text,
    is_conversation_stop_intent,
    extract_raw_text_from_event,
)
from clawhermes_lark.targets import (
    detect_id_type,
    normalize_feishu_target,
    format_feishu_target,
    resolve_receive_id_type,
    looks_like_feishu_id,
    normalize_message_id,
)
from clawhermes_lark.card_builder import (
    build_card,
    build_thinking_card,
    build_streaming_card,
    build_complete_card,
    build_confirm_card,
    build_markdown_element,
    split_reasoning_text,
    optimize_markdown_style,
    to_cardkit_v2,
)
from clawhermes_lark.tool_use_display import (
    build_tool_use_display,
    build_tool_use_summary,
    build_tool_use_title_suffix,
)
from clawhermes_lark.streaming_card import StreamingCardController
from clawhermes_lark.reply_dispatcher import (
    ReplyDispatcher,
    TypingIndicatorManager,
    create_reply_dispatcher,
    resolve_reply_mode,
    resolve_footer_config,
)
from clawhermes_lark.interactive import (
    InteractiveDispatcher,
    InteractiveHandler,
    InteractiveContext,
    InteractiveRespond,
    get_interactive_dispatcher,
)
from clawhermes_lark.accounts import (
    LarkAccount,
    get_lark_account,
    get_lark_account_ids,
    get_default_lark_account_id,
)
from clawhermes_lark.security import (
    collect_security_warnings,
    collect_isolation_warnings,
    validate_allow_from,
)
from clawhermes_lark.flush_controller import FlushController
from clawhermes_lark.card_error import (
    is_card_rate_limit_error,
    is_card_table_limit_error,
    sanitize_card_content,
)
from clawhermes_lark.footer_config import (
    resolve_footer_config as _resolve_footer_config,
    build_footer_text,
)

# ── OAPI 工具 ──────────────────────────────────────────────────────
from clawhermes_lark.oapi_tools import (
    OAPI_TOOL_REGISTRY,
    get_oapi_tool,
    list_oapi_tools,
    invoke_oapi_tool,
    sheets_get_meta,
    sheets_read_values,
    sheets_write_values,
    sheets_list,
    calendar_list,
    calendar_list_events,
    calendar_create_event,
    drive_list_files,
    drive_download_file,
    drive_search,
    wiki_list_spaces,
    wiki_get_node,
    wiki_list_nodes,
    docs_get_content,
    docs_get_meta,
    im_get_chat_info,
    im_list_chat_members,
    common_get_user,
    common_search_users,
    search_enterprise,
)

# ── Onboarding ─────────────────────────────────────────────────────
from clawhermes_lark.onboarding import (
    build_welcome_card,
    trigger_onboarding,
    trigger_onboarding_auth,
    handle_onboarding_card_action,
    is_onboarding_complete,
    mark_onboarding_complete,
    load_onboarding_state,
    save_onboarding_state,
)

__version__ = "0.3.0"

__all__ = [
    # Adapter
    "LarkAdapter",
    "LarkConfig",
    "LarkEventType",
    "create_lark_adapter",
    # Client
    "BotIdentity",
    "LarkClient",
    # Messaging
    "FeishuReaction",
    "TypingIndicator",
    "add_reaction",
    "remove_reaction",
    "list_reactions",
    "edit_message",
    "build_markdown_card",
    "send_card",
    # Chat queue
    "build_queue_key",
    "enqueue_feishu_chat_task",
    "get_active_dispatcher",
    "has_active_task",
    "register_active_dispatcher",
    "unregister_active_dispatcher",
    # Dedup
    "MessageDedup",
    "is_message_expired",
    "create_message_dedup",
    # Abort detect
    "is_abort_trigger",
    "is_likely_abort_text",
    "is_conversation_stop_intent",
    "extract_raw_text_from_event",
    # Targets
    "detect_id_type",
    "normalize_feishu_target",
    "format_feishu_target",
    "resolve_receive_id_type",
    "looks_like_feishu_id",
    "normalize_message_id",
    # Card builder
    "build_card",
    "build_thinking_card",
    "build_streaming_card",
    "build_complete_card",
    "build_confirm_card",
    "build_markdown_element",
    "split_reasoning_text",
    "optimize_markdown_style",
    "to_cardkit_v2",
    # Tool use display
    "build_tool_use_display",
    "build_tool_use_summary",
    "build_tool_use_title_suffix",
    # Streaming
    "StreamingCardController",
    # Reply dispatcher
    "ReplyDispatcher",
    "TypingIndicatorManager",
    "create_reply_dispatcher",
    "resolve_reply_mode",
    "resolve_footer_config",
    # Interactive
    "InteractiveDispatcher",
    "InteractiveHandler",
    "InteractiveContext",
    "InteractiveRespond",
    "get_interactive_dispatcher",
    # Accounts
    "LarkAccount",
    "get_lark_account",
    "get_lark_account_ids",
    "get_default_lark_account_id",
    # Security
    "collect_security_warnings",
    "collect_isolation_warnings",
    "validate_allow_from",
    # Flush / Card error
    "FlushController",
    "is_card_rate_limit_error",
    "is_card_table_limit_error",
    "sanitize_card_content",
    # Footer
    "build_footer_text",
    # OAPI Tools
    "OAPI_TOOL_REGISTRY",
    "get_oapi_tool",
    "list_oapi_tools",
    "invoke_oapi_tool",
    "sheets_get_meta",
    "sheets_read_values",
    "sheets_write_values",
    "sheets_list",
    "calendar_list",
    "calendar_list_events",
    "calendar_create_event",
    "drive_list_files",
    "drive_download_file",
    "drive_search",
    "wiki_list_spaces",
    "wiki_get_node",
    "wiki_list_nodes",
    "docs_get_content",
    "docs_get_meta",
    "im_get_chat_info",
    "im_list_chat_members",
    "common_get_user",
    "common_search_users",
    "search_enterprise",
    # Onboarding
    "build_welcome_card",
    "trigger_onboarding",
    "trigger_onboarding_auth",
    "handle_onboarding_card_action",
    "is_onboarding_complete",
    "mark_onboarding_complete",
    "load_onboarding_state",
    "save_onboarding_state",
]
