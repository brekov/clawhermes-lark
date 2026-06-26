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
├── __init__.py           # 公共 API 导出 (40+ symbols)
├── client.py             # LarkClient — 统一 SDK 管理器
│   ├── from_credentials()   # 凭证 → 客户端
│   ├── from_account()       # LarkAccount → 客户端 (含缓存)
│   ├── probe()              # 连接探测 (bot/v3/info)
│   └── get_bot_identity()   # 机器人身份获取
├── adapter.py            # LarkAdapter — ChannelAdapter 实现
│   ├── WebSocket 长连接 + 自动重连 (jitter)
│   ├── HTTP Webhook 消息回调
│   ├── 消息去重 (FIFO MessageDedup + TTL sweep)
│   ├── 按 chat 串行队列 (Chat Queue)
│   ├── 中止快速通道 (Abort Fast-Path)
│   ├── 权限门控 (group_policy / allowlist / @提及)
│   ├── 反应事件路由 (Reaction as synthetic event)
│   ├── 交互式卡片回调 (Card action dispatch)
│   └── 多账户支持 + app_id 所有权校验
├── messaging.py          # 消息交互增强
│   ├── Reactions (add / remove / list)
│   ├── TypingIndicator (模拟"正在输入")
│   ├── edit_message (编辑已发送消息)
│   └── Card 消息 (build_markdown_card / send_card)
├── chat_queue.py         # 按 chat 串行任务队列
│   ├── process-level singleton
│   ├── thread-scoped key 支持
│   └── ActiveDispatcher 注册 (abort fast-path)
├── dedup.py              # FIFO 消息去重
│   ├── TTL + 定期 sweep
│   ├── scope 前缀 (多账户)
│   └── 消息过期检查 (reconnect replay)
├── abort_detect.py       # 中止触发检测
│   ├── 精确触发词匹配 (40+ 语言)
│   ├── /stop 命令检测
│   └── 对话停止意图短语 (superset)
├── targets.py            # 目标 ID 解析与规范化
│   ├── ID 类型检测 (oc_ / ou_ / plain)
│   ├── 路由前缀剥离 (chat: / user: / feishu:)
│   ├── receive_id_type 解析
│   └── Message ID 规范化
├── card_builder.py       # 交互式卡片构建器
│   ├── 状态感知: thinking / streaming / complete / confirm
│   ├── Reasoning 文本分离
│   ├── Markdown 样式优化
│   └── CardKit V1 ↔ V2 转换
├── tool_use_display.py   # 工具调用卡片显示
│   ├── 工具描述符注册表 (10+ tools)
│   ├── 参数脱敏 (secret redaction)
│   └── 状态图标渲染 (⏳🔄✅❌🛑)
├── streaming_card.py     # 流式卡片控制器
│   ├── 状态机: idle → creating → streaming → completed / aborted / terminated
│   ├── 内容累积 + typewriter 效果
│   ├── Tool use 步骤集成
│   └── Flush 节流 + 错误处理
├── reply_dispatcher.py   # 回复分发工厂
│   ├── Reply mode 解析 (streaming / static / auto)
│   ├── TypingIndicatorManager
│   └── Footer 配置解析
├── interactive.py        # 交互式卡片回调分发
│   ├── card.action.trigger → handler pipeline
│   ├── respond helpers (reply / followUp / editMessage)
│   └── 去重 + namespace 路由
├── accounts.py           # 多账户支持
│   ├── LarkAccount 数据类
│   ├── 账户配置合并
│   └── enable/disable/delete 管理
├── security.py           # 安全检查
│   ├── 群策略安全警告
│   ├── 跨租户隔离检查
│   └── allow_from 校验
├── flush_controller.py   # 卡片刷新节流
├── card_error.py         # 卡片错误检测
│   ├── 速率限制 (22801)
│   ├── 表格限制 (17410)
│   └── 文本段安全化
├── footer_config.py      # 卡片页脚配置
└── hermes_vendor/        # Hermes 生产级消息引擎 (5,512 行)
├── oapi_tools.py         # OAPI 工具 (20 tools)
│   ├── Sheets: 电子表格读写
│   ├── Calendar: 日历事件管理
│   ├── Drive: 文件上传/下载/搜索
│   ├── Wiki: 知识库操作
│   ├── Docs: 文档内容管理
│   └── IM/Search/Common: 消息与搜索
├── onboarding.py         # 配对引导 + OAuth 预授权
│   ├── build_welcome_card: 引导欢迎卡片
│   ├── trigger_onboarding: 触发配对引导
│   └── 引导状态管理 + 持久化
    ├── feishu_hermes.py     # 消息解析 / Markdown 转换 / @提及
    └── _compat.py           # 向后兼容层
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
| `send_response(response, original)` | 发送回复消息 (支持 card/text/post) |
| `send_image(chat_id, image_data)` | 发送图片 |
| `send_file(chat_id, file_data, file_name)` | 发送文件 |
| `get_user_info(user_id)` | 获取用户信息 |
| `handle_webhook(body)` | HTTP Webhook 回调处理 |

### Card System

| 函数 | 说明 |
|:--|:--|
| `build_card(state, ...)` | 构建通用卡片 |
| `build_thinking_card(text)` | 构建思考中卡片 |
| `build_streaming_card(text, reasoning, tool_use)` | 构建流式卡片 |
| `build_complete_card(text, reasoning, tool_use, footer)` | 构建完成卡片 |
| `build_confirm_card(desc, confirm_val, cancel_val)` | 构建确认卡片 |
| `build_markdown_element(content, element_id)` | 构建 Markdown 元素 |
| `split_reasoning_text(text)` | 分离推理/回答文本 |
| `optimize_markdown_style(text)` | 优化 Feishu Markdown |
| `to_cardkit_v2(card)` | V1 → V2 格式转换 |

### Streaming

| 类/方法 | 说明 |
|:--|:--|
| `StreamingCardController` | 流式卡片生命周期管理 |
| `controller.start_thinking()` | 创建思考中卡片 |
| `controller.append_text(text)` | 追加流式文本 |
| `controller.complete(final_text)` | 完成流式输出 |
| `controller.abort(reason)` | 中止流式输出 |
| `controller.add_tool_step(step)` | 记录工具调用 |

### Interactive Dispatch

| 类/方法 | 说明 |
|:--|:--|
| `InteractiveDispatcher` | 卡片回调分发器 |
| `InteractiveHandler(namespace)` | 命名空间处理器 |
| `InteractiveContext` | 处理器上下文 |
| `InteractiveRespond` | 回复助手 (reply/followUp/editMessage/toast) |
| `get_interactive_dispatcher()` | 获取进程级单例 |

### Infrastructure

| 模块 | 函数/类 | 说明 |
|:--|:--|:--|
| `dedup` | `MessageDedup` | FIFO 消息去重 (TTL + sweep) |
| `dedup` | `is_message_expired(create_time)` | 消息过期检查 |
| `dedup` | `create_message_dedup()` | 创建去重实例 |
| `chat_queue` | `enqueue_feishu_chat_task(...)` | 串行队列入队 |
| `chat_queue` | `build_queue_key(account, chat, thread)` | 队列键构建 |
| `chat_queue` | `has_active_task(key)` | 活跃任务检查 |
| `abort_detect` | `is_abort_trigger(text)` | 精确触发匹配 |
| `abort_detect` | `is_likely_abort_text(text)` | 扩展检测 (+ /stop) |
| `abort_detect` | `is_conversation_stop_intent(text)` | 对话停止意图 |
| `abort_detect` | `extract_raw_text_from_event(event)` | 事件文本提取 |
| `targets` | `normalize_feishu_target(raw)` | 目标 ID 规范化 |
| `targets` | `resolve_receive_id_type(id)` | API receive_id_type 解析 |
| `targets` | `looks_like_feishu_id(raw)` | Feishu ID 判定 |
| `targets` | `normalize_message_id(msg_id)` | 消息 ID 规范化 |

### Security & Accounts

| 函数 | 说明 |
|:--|:--|
| `collect_security_warnings(config, account_id)` | 收集安全检查警告 |
| `collect_isolation_warnings(config)` | 多账户租户隔离检查 |
| `validate_allow_from(allow_from)` | 规范化 allow_from 列表 |
| `get_lark_account(config, account_id)` | 解析 LarkAccount |
| `get_lark_account_ids(config)` | 获取所有账户 ID |
| `get_default_lark_account_id(config)` | 获取默认账户 ID |

### OAPI Tools

| 函数 | 说明 |
|:--|:--|
|  | 获取电子表格元数据 |
|  | 读取表格范围的值 |
|  | 写入表格范围的值 |
|  | 列出可访问的电子表格 |
|  | 列出日历 |
|  | 列出日历事件 |
|  | 创建日历事件 |
|  | 列出 Drive 文件 |
|  | 下载 Drive 文件 |
|  | 搜索 Drive 文件 |
|  | 列出知识库空间 |
|  | 获取 Wiki 页面 |
|  | 列出 Wiki 节点 |
|  | 获取文档内容 |
|  | 获取文档元数据 |
|  | 获取群聊信息 |
|  | 列出群成员 |
|  | 获取用户信息 |
|  | 搜索用户 |
|  | 企业搜索 |

所有工具可通过注册表调用：


### Onboarding

| 函数 | 说明 |
|:--|:--|
|  | 触发配对引导流程，发送欢迎卡片 |
|  | 触发 OAuth 预授权流程 |
|  | 构建引导欢迎卡片 |
|  | 处理引导卡片的交互回调 |
|  | 检查引导是否已完成 |
|  | 标记引导已完成 |
|  | 从磁盘加载引导状态 |
|  | 持久化引导状态到磁盘 |

使用方式：


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
    domain: str = "feishu"          # "feishu" | "lark"
    connection_mode: str = "websocket"  # "websocket" | "webhook"

    # 群聊策略
    group_policy: str = "allowlist"  # "allowlist" | "open" | "disabled" | "admin_only" | "blacklist"
    allowed_group_users: list[str]   # 白名单/黑名单用户
    admins: list[str]                # 管理员 (总是允许)
    allow_bots: str = "none"         # "none" | "mentions" | "all"
    require_mention: bool = True     # 群聊中是否需要 @提及

    # Reaction 通知
    reactions_enabled: bool = True
    reaction_notifications: str = "own"  # "off" | "own" | "all"

    # 多账户
    account_id: str = "default"
```

### 环境变量

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export FEISHU_DOMAIN=feishu            # feishu | lark
export FEISHU_GROUP_POLICY=allowlist   # allowlist | open | disabled
```

### 多账户配置

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

适配器支持以下飞书事件类型（通过 WebSocket 长连接或 Webhook 接收）：

| 事件 | 处理方式 |
|:--|:--|
| `im.message.receive_v1` | 串行队列 → 权限门控 → 消息分发 |
| `im.message.read_v1` | 日志记录 |
| `im.message.reaction.created_v1` | Reaction → synthetic event → 分发 (own/all) |
| `im.message.reaction.deleted_v1` | 日志记录 |
| `im.chat.member.bot.added_v1` | 日志记录 |
| `im.chat.member.bot.deleted_v1` | 日志记录 |
| `card.action.trigger` | InteractiveDispatcher → 注册 handler 分发 |
| `url_verification` | Challenge 响应 (Webhook 模式) |

### Reaction 事件流

Reactions 通过 `reaction_notifications` 控制:
- `"off"` — 静默忽略
- `"own"` — 仅 Bot 自己发送的消息上的反应才生成 synthetic event
- `"all"` — 所有消息上的反应都生成 synthetic event

Synthetic event 格式: `[Reaction] {emoji_type}`

## 消息处理管道

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ WS Event │ → │ Self-Echo │ → │  Dedup   │ → │  Expiry  │ → │  Abort   │
│ Receive  │    │  Filter   │    │ (FIFO)   │    │  Check   │    │ Fast-Path│
└──────────┘    └───────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                       ↓
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Message  │ ← │  @Mention  │ ← │  Content  │ ← │  Gate    │ ← │  Serial  │
│ Dispatch │    │   Check    │    │  Extract  │    │ (Policy) │    │  Queue   │
└──────────┘    └───────────┘    └──────────┘    └──────────┘    └──────────┘
```

## 对齐状态

| 功能 | larksuite/openclaw-lark | clawhermes-lark v0.2.0 |
|:--|:--|:--|
| SDK Client | `LarkClient` + 多账户缓存 | `LarkClient` + `from_account()` ✅ |
| 连接探测 | `probeFeishu()` | `probe()` ✅ |
| Bot 身份 | bot/v3/info | `get_bot_identity()` ✅ |
| WebSocket | 长连接 + 自动重连 + jitter | ✅ |
| Webhook | HTTP 回调 + 签名验证 | ✅ |
| 消息去重 | FIFO + TTL + sweep | `MessageDedup` ✅ |
| 串行队列 | chat queue + thread-scoped | `enqueue_feishu_chat_task()` ✅ |
| 中止快速通道 | abort fast-path | ✅ |
| Reactions | 添加/移除/列出 | ✅ |
| Reaction 事件 | synthetic event dispatch | ✅ (own/all/off) |
| Typing 指示 | 时钟 emoji 模拟 | `TypingIndicator` ✅ |
| 消息编辑 | PATCH API | `edit_message()` ✅ |
| Card 消息 | Markdown card builder | `build_card()` + streaming ✅ |
| 流式卡片 | StreamingCardController | ✅ (state machine) |
| 交互式卡片 | card.action.trigger dispatch | `InteractiveDispatcher` ✅ |
| 工具调用显示 | ToolUseDisplay | `build_tool_use_display()` ✅ |
| Reasoning 分离 | splitReasoningText | `split_reasoning_text()` ✅ |
| 群聊策略 | allowlist/open/disabled/admin_only | ✅ |
| @提及 | 精确匹配 + 回退 | ✅ |
| 多账户 | accounts + isolation | ✅ |
| 安全检查 | collectWarnings | `collect_security_warnings()` ✅ |
| 目标规范化 | normalizeFeishuTarget | `normalize_feishu_target()` ✅ |
| 卡片刷新节流 | FlushController | ✅ |
| 卡片错误处理 | rate/table limit detection | ✅ |
| Footer 配置 | 可配置元数据显示 | ✅ |
| reply mode | streaming / static / auto | ✅ |
| OAPI 工具 | docs/calendar/sheets/wiki/drive/im | ✅ (20 tools) |
| Onboarding | pairing welcome card + OAuth stub | ✅ |
| VC Meeting | vc.bot.meeting_invited handler | ✅ (stub) |
| Drive Comment | drive.notice.comment handler | ✅ (stub) |

## 许可

MIT — 同 ClawHermes 项目
