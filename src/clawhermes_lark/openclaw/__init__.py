"""
ClawHermes-Lark openclaw 对齐层 — 功能对齐 larksuite/openclaw-lark

本层实现了 larksuite/openclaw-lark 飞书消息渠道的全部核心功能和交互逻辑：
  - 消息管道: 去重、串行队列、中止检测、目标规范化
  - 卡片系统: Card Builder、Streaming Card、Reply Dispatcher、Tool Use Display
  - 交互层: Interactive Dispatch (card.action.trigger)
  - 工具层: OAPI Tools (sheets/calendar/drive/wiki/docs/im/search)
  - 引导层: Onboarding (配对欢迎卡 + OAuth)
  - 账户与安全: 多账户、安全检查
"""
# ── 消息管道 ───────────────────────────────────────────────────────
from clawhermes_lark.openclaw.chat_queue import (
    ActiveDispatcherEntry,
    build_queue_key,
    enqueue_feishu_chat_task,
    get_active_dispatcher,
    has_active_task,
    register_active_dispatcher,
    unregister_active_dispatcher,
)
from clawhermes_lark.openclaw.dedup import MessageDedup, is_message_expired, create_message_dedup
from clawhermes_lark.openclaw.abort_detect import (
    is_abort_trigger,
    is_likely_abort_text,
    is_conversation_stop_intent,
    extract_raw_text_from_event,
)
from clawhermes_lark.openclaw.targets import (
    detect_id_type,
    normalize_feishu_target,
    format_feishu_target,
    resolve_receive_id_type,
    looks_like_feishu_id,
    normalize_message_id,
)

# ── 消息交互 ───────────────────────────────────────────────────────
from clawhermes_lark.openclaw.messaging import (
    FeishuReaction,
    TypingIndicator,
    add_reaction,
    remove_reaction,
    list_reactions,
    edit_message,
    build_markdown_card,
    send_card,
)

# ── 卡片系统 ───────────────────────────────────────────────────────
from clawhermes_lark.openclaw.card_builder import (
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
from clawhermes_lark.openclaw.tool_use_display import (
    build_tool_use_display,
    build_tool_use_summary,
    build_tool_use_title_suffix,
)
from clawhermes_lark.openclaw.streaming_card import StreamingCardController
from clawhermes_lark.openclaw.reply_dispatcher import (
    ReplyDispatcher,
    TypingIndicatorManager,
    create_reply_dispatcher,
    resolve_reply_mode,
    resolve_footer_config,
)
from clawhermes_lark.openclaw.flush_controller import FlushController
from clawhermes_lark.openclaw.card_error import (
    is_card_rate_limit_error,
    is_card_table_limit_error,
    sanitize_card_content,
)
from clawhermes_lark.openclaw.footer_config import (
    resolve_footer_config,
    build_footer_text,
)

# ── 交互 ───────────────────────────────────────────────────────────
from clawhermes_lark.openclaw.interactive import (
    InteractiveDispatcher,
    InteractiveHandler,
    InteractiveContext,
    InteractiveRespond,
    get_interactive_dispatcher,
)

# ── 账户与安全 ─────────────────────────────────────────────────────
from clawhermes_lark.openclaw.accounts import (
    LarkAccount,
    get_lark_account,
    get_lark_account_ids,
    get_default_lark_account_id,
)
from clawhermes_lark.openclaw.security import (
    collect_security_warnings,
    collect_isolation_warnings,
    validate_allow_from,
)

# ── OAPI 工具 ──────────────────────────────────────────────────────
from clawhermes_lark.openclaw.oapi_tools import (
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
from clawhermes_lark.openclaw.onboarding import (
    build_welcome_card,
    trigger_onboarding,
    trigger_onboarding_auth,
    handle_onboarding_card_action,
    is_onboarding_complete,
    mark_onboarding_complete,
    load_onboarding_state,
    save_onboarding_state,
)
