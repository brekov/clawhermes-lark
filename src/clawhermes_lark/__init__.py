"""
ClawHermes-Lark — 飞书/Lark 渠道适配器 v0.3.0

分层架构：
  adapter/        — ClawHermes ChannelAdapter 适配层
  openclaw_lark/  — 功能对齐 larksuite/openclaw-lark
  hermes_vendor/  — 复用 NousResearch/hermes-agent 消息解析引擎

独立 pip 包：pip install clawhermes-lark
"""

# ── Layer 1: ClawHermes 适配层 ──
from clawhermes_lark.adapter.adapter import (
    LarkAdapter, LarkConfig, LarkEventType, create_lark_adapter,
)
from clawhermes_lark.adapter.client import BotIdentity, LarkClient

# ── Layer 2: openclaw-lark 对齐（全量 re-export）──
from clawhermes_lark.openclaw_lark.channel import (
    ActiveDispatcherEntry, build_queue_key, enqueue_feishu_chat_task,
    get_active_dispatcher, has_active_task,
    register_active_dispatcher, unregister_active_dispatcher,
    MessageDedup, is_message_expired, create_message_dedup,
    is_abort_trigger, is_likely_abort_text, is_conversation_stop_intent,
    extract_raw_text_from_event,
    detect_id_type, normalize_feishu_target, format_feishu_target,
    resolve_receive_id_type, looks_like_feishu_id, normalize_message_id,
    InteractiveDispatcher, InteractiveHandler, InteractiveContext,
    InteractiveRespond, get_interactive_dispatcher,
)
from clawhermes_lark.openclaw_lark.card import (
    build_card, build_thinking_card, build_streaming_card,
    build_complete_card, build_confirm_card, build_markdown_element,
    split_reasoning_text, optimize_markdown_style, to_cardkit_v2,
    StreamingCardController,
    ReplyDispatcher, TypingIndicatorManager, create_reply_dispatcher,
    resolve_reply_mode, resolve_footer_config,
    build_tool_use_display, build_tool_use_summary, build_tool_use_title_suffix,
    FlushController,
    is_card_rate_limit_error, is_card_table_limit_error, sanitize_card_content,
)
from clawhermes_lark.openclaw_lark.messaging import (
    FeishuReaction, TypingIndicator,
    add_reaction, remove_reaction, list_reactions,
    edit_message, build_markdown_card, send_card,
)
from clawhermes_lark.openclaw_lark.core import (
    LarkAccount, get_lark_account, get_lark_account_ids,
    get_default_lark_account_id,
    collect_security_warnings, collect_isolation_warnings, validate_allow_from,
)
from clawhermes_lark.openclaw_lark.tools import (
    OAPI_TOOL_REGISTRY, get_oapi_tool, list_oapi_tools, invoke_oapi_tool,
    sheets_get_meta, sheets_read_values, sheets_write_values, sheets_list,
    calendar_list, calendar_list_events, calendar_create_event,
    drive_list_files, drive_download_file, drive_search,
    wiki_list_spaces, wiki_get_node, wiki_list_nodes,
    docs_get_content, docs_get_meta,
    im_get_chat_info, im_list_chat_members,
    common_get_user, common_search_users, search_enterprise,
    build_welcome_card, trigger_onboarding, trigger_onboarding_auth,
    handle_onboarding_card_action, is_onboarding_complete,
    mark_onboarding_complete, load_onboarding_state, save_onboarding_state,
)

__version__ = "0.3.0"
