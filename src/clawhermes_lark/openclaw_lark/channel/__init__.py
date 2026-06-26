"""channel/ — 消息管道基础设施（对齐 openclaw-lark src/channel/）"""
from clawhermes_lark.openclaw_lark.channel.chat_queue import (
    ActiveDispatcherEntry, build_queue_key, enqueue_feishu_chat_task,
    get_active_dispatcher, has_active_task,
    register_active_dispatcher, unregister_active_dispatcher,
)
from clawhermes_lark.openclaw_lark.channel.dedup import (
    MessageDedup, is_message_expired, create_message_dedup,
)
from clawhermes_lark.openclaw_lark.channel.abort_detect import (
    is_abort_trigger, is_likely_abort_text, is_conversation_stop_intent,
    extract_raw_text_from_event,
)
from clawhermes_lark.openclaw_lark.channel.targets import (
    detect_id_type, normalize_feishu_target, format_feishu_target,
    resolve_receive_id_type, looks_like_feishu_id, normalize_message_id,
)
from clawhermes_lark.openclaw_lark.channel.interactive import (
    InteractiveDispatcher, InteractiveHandler, InteractiveContext,
    InteractiveRespond, get_interactive_dispatcher,
)
