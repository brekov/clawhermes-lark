"""Hermes Feishu adapter vendor — 从 NousResearch/hermes-agent 复刻

导入顺序至关重要：
  1. _compat._install_compat_modules() 注入 sys.modules
  2. 然后才能安全导入 feishu_hermes（它的顶层 import 依赖注入后的模块）
"""
from clawhermes_lark.hermes_vendor._compat import _install_compat_modules

# 安装兼容层 — 必须在 feishu_hermes 导入前执行
_install_compat_modules()

# 安全导入 feishu_hermes
from clawhermes_lark.hermes_vendor.feishu_hermes import (  # noqa: F401
    FeishuAdapter,
    FeishuAdapterSettings,
    FeishuBatchState,
    FeishuGroupRule,
    FeishuMentionRef,
    FeishuNormalizedMessage,
    FeishuPostMediaRef,
    FeishuPostParseResult,
    _build_markdown_post_payload,
    _build_markdown_post_rows,
    _build_mentions_map,
    _escape_markdown_text,
    _extract_mention_ids,
    _strip_markdown_to_plain_text,
    check_feishu_requirements,
    normalize_feishu_message,
    parse_feishu_post_payload,
)
