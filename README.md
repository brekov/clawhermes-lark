# clawhermes-lark

飞书/Lark 渠道适配器 — 完整对齐 [larksuite/openclaw-lark](https://github.com/larksuite/openclaw-lark)

基于 [lark-oapi](https://pypi.org/project/lark-oapi/) 官方 Python SDK，为 ClawHermes Agent 框架提供飞书消息渠道支持。

## 安装

```bash
pip install -e ./clawhermes-lark
```

## 架构

```
clawhermes_lark/
├── adapter/                     # Layer 1: ClawHermes 适配层
│   ├── adapter.py               #   LarkAdapter — ChannelAdapter 实现
│   └── client.py                #   LarkClient — 多账户 SDK 管理器
│
├── openclaw_lark/              # Layer 2: 对齐 larksuite/openclaw-lark
│   ├── channel/                 #   src/channel/
│   │   ├── chat_queue.py        #     串行任务队列
│   │   ├── dedup.py             #     FIFO 消息去重
│   │   ├── abort_detect.py      #     中止触发检测
│   │   ├── targets.py           #     ID 规范化
│   │   └── interactive.py       #     交互式卡片分发
│   ├── card/                    #   src/card/
│   │   ├── builder.py           #     交互式卡片构建 (4 种状态)
│   │   ├── streaming.py         #     流式卡片状态机
│   │   ├── reply.py             #     回复分发工厂
│   │   ├── tool_use.py          #     工具调用卡片显示
│   │   ├── flush.py             #     卡片刷新节流
│   │   ├── error.py             #     卡片错误检测
│   │   └── footer.py            #     卡片页脚配置
│   ├── messaging/               #   src/messaging/
│   │   └── messaging.py         #     Reactions / Typing / Edit / Card
│   ├── core/                    #   src/core/
│   │   ├── accounts.py          #     多账户支持
│   │   └── security.py          #     安全检查
│   └── tools/                   #   src/tools/
│       ├── oapi_tools.py        #     OAPI 工具 (20 tools)
│       └── onboarding.py        #     配对引导 + OAuth
│
└── hermes_vendor/               # Layer 3: 复用 NousResearch/hermes-agent
    ├── feishu_hermes.py         #   消息解析 / Markdown 转换 / @提及
    └── _compat.py               #   独立运行兼容层
```

三层架构对应项目的三个关注点：

| 目录 | 来源 | 作用 |
|------|------|------|
| `adapter/` | ClawHermes 框架 | 实现 `ChannelAdapter` 接口，对接 Agent 框架 |
| `openclaw_lark/` | larksuite/openclaw-lark | 对齐字节官方飞书渠道的全部功能和交互逻辑 |
| `hermes_vendor/` | NousResearch/hermes-agent | 复用消息解析引擎（parse/post/markdown 转换） |

## 快速开始

```python
from clawhermes_lark import LarkClient, LarkAdapter, LarkConfig

# 1. 连接探测
client = LarkClient.from_credentials(
    app_id="cli_xxx",
    app_secret="xxx",
    domain="feishu",
)
identity = await client.get_bot_identity()
print(f"Connected as {identity.name}")

# 2. 渠道适配器
config = LarkConfig(
    app_id="cli_xxx",
    app_secret="xxx",
    domain="feishu",
    group_policy="allowlist",
    reaction_notifications="own",
)
adapter = LarkAdapter(config)
await adapter.start()

# 3. 消息交互
from clawhermes_lark import TypingIndicator, add_reaction
async with TypingIndicator(client, message_id):
    await process_request()

# 4. 卡片系统
from clawhermes_lark import build_card, StreamingCardController

# 5. OAPI 工具
from clawhermes_lark import list_oapi_tools, invoke_oapi_tool
result = await invoke_oapi_tool(client, "sheets_read_values", {
    "spreadsheet_token": "shtcnxxx",
    "sheet_id": "abc123",
    "range": "A1:C10",
})

# 6. 引导
from clawhermes_lark import trigger_onboarding
await trigger_onboarding(adapter, user_open_id="ou_xxx", bot_name="MyBot")
```

## API 参考

### LarkClient

| 方法 | 说明 |
|:--|:--|
| `from_credentials(app_id, app_secret, domain, account_id)` | 从凭证创建客户端 |
| `from_account(account)` | 从 LarkAccount 创建 (含缓存) |
| `get(account_id)` | 按 accountId 查找缓存实例 |
| `clear_cache(account_id)` | 清除缓存 |
| `probe()` / `probe_sync()` | 连接探测 (bot/v3/info) |
| `get_bot_identity()` / `get_bot_identity_sync()` | 获取机器人身份 |
| `sdk` | 获取底层 lark_oapi.Client |

### LarkAdapter

| 方法 | 说明 |
|:--|:--|
| `start()` | 启动连接 (WebSocket 或 Webhook) |
| `stop()` | 断开连接 |
| `send_response(response, original)` | 发送回复 (card / text / post) |
| `send_image(chat_id, image_data)` | 发送图片 |
| `send_file(chat_id, file_data, file_name)` | 发送文件 |
| `get_user_info(user_id)` | 获取用户信息 |
| `handle_webhook(body)` | HTTP Webhook 回调处理 |

### Channel — 消息管道

| 模块 | 函数/类 | 说明 |
|:--|:--|:--|
| `channel/chat_queue` | `enqueue_feishu_chat_task()` | 串行队列入队 |
| `channel/chat_queue` | `build_queue_key(account, chat, thread)` | 队列键构建 |
| `channel/chat_queue` | `has_active_task(key)` | 活跃任务检查 |
| `channel/dedup` | `MessageDedup` | FIFO 去重 (TTL + sweep) |
| `channel/dedup` | `is_message_expired(ts)` | 消息过期检查 |
| `channel/abort_detect` | `is_abort_trigger(text)` | 精确触发匹配 |
| `channel/abort_detect` | `is_likely_abort_text(text)` | 扩展检测 (+ /stop) |
| `channel/abort_detect` | `is_conversation_stop_intent(text)` | 对话停止意图 |
| `channel/abort_detect` | `extract_raw_text_from_event(event)` | 事件文本提取 |
| `channel/targets` | `normalize_feishu_target(raw)` | 目标 ID 规范化 |
| `channel/targets` | `resolve_receive_id_type(id)` | API receive_id_type 解析 |
| `channel/targets` | `looks_like_feishu_id(raw)` | Feishu ID 判定 |
| `channel/interactive` | `InteractiveDispatcher` | 卡片回调分发器 |
| `channel/interactive` | `InteractiveHandler(ns)` | 命名空间处理器 |
| `channel/interactive` | `InteractiveRespond` | 回复助手 |

### Card — 卡片系统

| 模块 | 函数/类 | 说明 |
|:--|:--|:--|
| `card/builder` | `build_card(state, ...)` | 构建通用卡片 |
| `card/builder` | `build_thinking_card(text)` | 思考中卡片 |
| `card/builder` | `build_streaming_card(...)` | 流式卡片 |
| `card/builder` | `build_complete_card(...)` | 完成卡片 |
| `card/builder` | `build_confirm_card(...)` | 确认卡片 |
| `card/builder` | `split_reasoning_text(text)` | 分离推理文本 |
| `card/builder` | `to_cardkit_v2(card)` | V1 → V2 转换 |
| `card/streaming` | `StreamingCardController` | 流式卡片状态机 |
| `card/reply` | `create_reply_dispatcher(...)` | 回复分发工厂 |
| `card/reply` | `resolve_reply_mode(cfg, type)` | 回复模式解析 |
| `card/tool_use` | `build_tool_use_display(...)` | 工具调用显示 |
| `card/tool_use` | `build_tool_use_summary(steps)` | 工具摘要 |
| `card/flush` | `FlushController` | 卡片刷新节流 |
| `card/error` | `is_card_rate_limit_error(code)` | 速率限制检测 |
| `card/error` | `sanitize_card_content(text)` | 文本安全化 |
| `card/footer` | `build_footer_text(cfg, ...)` | 页脚构建 |

### Messaging — 消息交互

| 函数 | 说明 |
|:--|:--|
| `add_reaction(client, message_id, emoji)` | 添加表情回应 |
| `remove_reaction(client, message_id, reaction_id)` | 移除表情回应 |
| `list_reactions(client, message_id)` | 列出所有回应 |
| `edit_message(client, message_id, content)` | 编辑消息 |
| `build_markdown_card(text)` | 构建 Markdown 卡片 |
| `send_card(client, receive_id, card)` | 发送卡片 |
| `TypingIndicator(client, message_id)` | 模拟"正在输入" |

### OAPI Tools — 工具层 (20 tools)

| 类别 | 函数 | 说明 |
|:--|:--|:--|
| Sheets | `sheets_get_meta` | 获取表格元数据 |
| Sheets | `sheets_read_values` | 读取表格范围 |
| Sheets | `sheets_write_values` | 写入表格范围 |
| Sheets | `sheets_list` | 列出电子表格 |
| Calendar | `calendar_list` | 列出日历 |
| Calendar | `calendar_list_events` | 列出事件 |
| Calendar | `calendar_create_event` | 创建事件 |
| Drive | `drive_list_files` | 列出文件 |
| Drive | `drive_download_file` | 下载文件 |
| Drive | `drive_search` | 搜索文件 |
| Wiki | `wiki_list_spaces` | 列出知识库 |
| Wiki | `wiki_get_node` | 获取页面 |
| Wiki | `wiki_list_nodes` | 列出节点 |
| Docs | `docs_get_content` | 获取文档内容 |
| Docs | `docs_get_meta` | 获取文档元数据 |
| IM | `im_get_chat_info` | 获取群聊信息 |
| IM | `im_list_chat_members` | 列出群成员 |
| Common | `common_get_user` | 获取用户信息 |
| Common | `common_search_users` | 搜索用户 |
| Search | `search_enterprise` | 企业搜索 |

通过注册表调用：
```python
from clawhermes_lark import list_oapi_tools, invoke_oapi_tool
tools = list_oapi_tools()                     # 列出所有工具
result = await invoke_oapi_tool(client,       # 按名称调用
    "sheets_read_values",
    {"spreadsheet_token": "shtcnxxx", "sheet_id": "abc", "range": "A1:C10"})
```

### Onboarding — 引导

| 函数 | 说明 |
|:--|:--|
| `trigger_onboarding(adapter, user_open_id, ...)` | 触发配对引导流程 |
| `build_welcome_card(bot_name, domain, ...)` | 构建欢迎卡片 |
| `handle_onboarding_card_action(action, ctx)` | 引导卡片交互处理 |
| `is_onboarding_complete(account_id)` | 检查引导状态 |
| `mark_onboarding_complete(account_id)` | 标记引导完成 |
| `load_onboarding_state(dir)` / `save_onboarding_state(dir)` | 状态持久化 |

### Core — 核心

| 函数 | 说明 |
|:--|:--|
| `get_lark_account(config, account_id)` | 解析 LarkAccount |
| `get_lark_account_ids(config)` | 获取所有账户 ID |
| `collect_security_warnings(config, account_id)` | 收集安全检查警告 |
| `collect_isolation_warnings(config)` | 多账户租户隔离检查 |

## 配置

### LarkConfig

```python
@dataclass
class LarkConfig:
    # 凭证 (必填)
    app_id: str
    app_secret: str

    # 安全
    verification_token: str = ""
    encrypt_key: str = ""

    # 域名 / 连接模式
    domain: str = "feishu"               # "feishu" | "lark"
    connection_mode: str = "websocket"   # "websocket" | "webhook"

    # 群聊策略
    group_policy: str = "allowlist"      # allowlist | open | disabled | admin_only | blacklist
    allowed_group_users: list[str]
    admins: list[str]
    allow_bots: str = "none"             # none | mentions | all
    require_mention: bool = True

    # Reaction 通知
    reactions_enabled: bool = True
    reaction_notifications: str = "own"  # off | own | all

    # 多账户
    account_id: str = "default"
```

### 环境变量

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export FEISHU_DOMAIN=feishu
export FEISHU_GROUP_POLICY=allowlist
```

### 多账户

```json
{
  "channels": {
    "feishu": {
      "appId": "cli_default",
      "appSecret": "secret_default",
      "accounts": {
        "account2": {
          "appId": "cli_account2",
          "appSecret": "secret_account2",
          "groupPolicy": "open"
        }
      }
    }
  }
}
```

## 事件处理

| 事件类型 | 处理方式 |
|:--|:--|
| `im.message.receive_v1` | 串行队列 → 权限门控 → 消息分发 |
| `im.message.read_v1` | 日志记录 |
| `im.message.reaction.created_v1` | synthetic event 分发 (own/all 模式) |
| `im.message.reaction.deleted_v1` | 日志记录 |
| `im.chat.member.bot.added_v1` | 日志记录 |
| `im.chat.member.bot.deleted_v1` | 日志记录 |
| `card.action.trigger` | InteractiveDispatcher → handler 分发 |
| `vc.bot.meeting_invited_v1` | 日志记录 (stub) |
| `drive.notice.comment_add_v1` | 日志记录 (stub) |
| `url_verification` | Challenge 响应 (Webhook) |

## 消息处理管道

```
WebSocket Event
       │
       ▼
┌───────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Self-Echo │ → │  Dedup   │ → │  Expiry  │ → │  Abort   │
│  Filter   │   │ (FIFO)   │   │  Check   │   │ Fast-Path│
└───────────┘   └──────────┘   └──────────┘   └──────────┘
                                                      │
       ┌──────────────────────────────────────────────┘
       ▼
┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
│ Serial   │ → │   Gate    │ → │ @Mention │ → │ Message  │
│  Queue   │   │ (Policy)  │   │  Check   │   │ Dispatch │
└──────────┘   └───────────┘   └──────────┘   └──────────┘
```

## 对齐状态

| 功能 | larksuite/openclaw-lark | clawhermes-lark |
|:--|:--|:--|
| SDK Client | `LarkClient` + 多账户 | ✅ |
| 连接探测 | `probeFeishu()` | ✅ |
| Bot 身份 | bot/v3/info | ✅ |
| WebSocket | 长连接 + 自动重连 | ✅ |
| Webhook | HTTP 回调 + 签名验证 | ✅ |
| 消息去重 | FIFO + TTL + sweep | ✅ |
| 串行队列 | chat queue | ✅ |
| 中止快速通道 | abort fast-path | ✅ |
| Reactions | 添加/移除/列出 | ✅ |
| Reaction 事件 | synthetic event | ✅ |
| Typing 指示 | 表情模拟 | ✅ |
| 消息编辑 | PATCH API | ✅ |
| Card 消息 | builder + streaming | ✅ |
| 流式卡片 | StreamingCardController | ✅ |
| 交互式卡片 | card.action.trigger | ✅ |
| 工具调用显示 | ToolUseDisplay | ✅ |
| Reasoning 分离 | splitReasoningText | ✅ |
| 群聊策略 | allowlist/open/disabled/admin_only | ✅ |
| @提及 | 精确匹配 + 回退 | ✅ |
| 多账户 | accounts + isolation | ✅ |
| 安全检查 | collectWarnings | ✅ |
| 目标规范化 | normalizeFeishuTarget | ✅ |
| 卡片刷新节流 | FlushController | ✅ |
| 卡片错误处理 | rate/table limit | ✅ |
| Footer 配置 | 可配置元数据 | ✅ |
| reply mode | streaming / static / auto | ✅ |
| OAPI 工具 | sheets/calendar/drive/wiki/docs/im | ✅ (20) |
| Onboarding | 配对引导 + OAuth | ✅ |
| VC Meeting | vc.bot.meeting_invited | ✅ (stub) |
| Drive Comment | drive.notice.comment | ✅ (stub) |

## 许可

MIT — 同 ClawHermes 项目
