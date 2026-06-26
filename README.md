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
│   ├── adapter.py               #   LarkAdapter — ChannelAdapter 实现 (36 methods)
│   └── client.py                #   LarkClient — 多账户 SDK 管理器
│
├── openclaw_lark/              # Layer 2: 对齐 larksuite/openclaw-lark (31 modules)
│   ├── channel/                 #   src/channel/
│   │   ├── chat_queue.py        #     串行任务队列
│   │   ├── dedup.py             #     FIFO 消息去重 (TTL + sweep)
│   │   ├── abort_detect.py      #     中止触发检测 (40+ 语言)
│   │   ├── targets.py           #     ID 规范化 (chat:/user:/feishu:)
│   │   ├── interactive.py       #     交互式卡片分发
│   │   └── directory.py         #     用户/群组目录查询
│   ├── card/                    #   src/card/
│   │   ├── builder.py           #     交互式卡片构建 (4 种状态)
│   │   ├── streaming.py         #     流式卡片状态机
│   │   ├── reply.py             #     回复分发工厂
│   │   ├── tool_use.py          #     工具调用卡片显示
│   │   ├── flush.py             #     卡片刷新节流
│   │   ├── error.py             #     卡片错误检测
│   │   ├── footer.py            #     卡片页脚配置
│   │   ├── markdown_style.py    #     完整 Markdown 飞书样式优化
│   │   ├── unavailable_guard.py #     消息不可用检测 + 降级
│   │   └── cardkit.py           #     CardKit 实体管理
│   ├── messaging/               #   src/messaging/
│   │   ├── messaging.py         #     Reactions / Typing / Edit / Card
│   │   ├── converter.py         #     消息格式转换 (text↔post↔card)
│   │   └── message_lookup.py    #     消息查询 API
│   ├── core/                    #   src/core/
│   │   ├── accounts.py          #     多账户支持
│   │   ├── security.py          #     安全检查
│   │   ├── device_flow.py       #     OAuth Device Flow (用户授权)
│   │   ├── app_registration.py  #     扫码创建飞书应用
│   │   ├── lark_ticket.py       #     请求追踪票据
│   │   ├── token_store.py       #     OAuth Token 持久化
│   │   ├── scope_manager.py     #     Scope 权限管理
│   │   └── tool_scopes.py       #     工具 Scope 定义
│   └── tools/                   #   src/tools/
│       ├── helpers.py           #     共享辅助函数
│       ├── oapi_tools.py        #     OAPI 工具 (20 tools)
│       ├── onboarding.py        #     配对引导 + 配置向导 + Device Flow
│       └── ask_user_question.py #     AI 表单提问工具
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
client = LarkClient.from_credentials(app_id="cli_xxx", app_secret="xxx", domain="feishu")
identity = await client.get_bot_identity()

# 2. 渠道适配器
config = LarkConfig(app_id="cli_xxx", app_secret="xxx", domain="feishu",
                    group_policy="allowlist", reaction_notifications="own")
adapter = LarkAdapter(config)
await adapter.start()

# 3. 扫码创建飞书应用 (App Registration)
from clawhermes_lark import run_setup_qr_code_flow
result = await run_setup_qr_code_flow(brand="feishu")
# → 终端显示 QR 码 → 用户飞书扫码 → 自动创建应用
# → 返回 {"ok": True, "app_id": "...", "app_secret": "...", "open_id": "..."}

# 4. OAPI 工具
from clawhermes_lark import invoke_oapi_tool
result = await invoke_oapi_tool(client, "sheets_read_values",
    {"spreadsheet_token": "shtcnxxx", "sheet_id": "abc", "range": "A1:C10"})
```

## 配置飞书 Bot（两种方式）

### 方式一：扫码创建（推荐）

对齐 openclaw-lark 的 `runNewAppFlow`：

```
1. app_registration_init(brand)        → 检查环境
2. app_registration_begin(brand)       → 生成 QR 码 + device_code
3. render_qr_terminal(url)             → 终端展示 QR 码
4. app_registration_poll(brand, code)  → 轮询 → 返回 appId + appSecret + openId
```

用户用飞书 App 扫描 QR 码 → 授权 → 飞书自动创建企业自建应用 → 自动下发凭证。

```python
from clawhermes_lark import run_setup_qr_code_flow
result = await run_setup_qr_code_flow(brand="feishu")
if result["ok"]:
    print(f"App ID: {result['app_id']}")
    print(f"App Secret: {result['app_secret']}")
```

### 方式二：手动配置

用户在 [open.feishu.cn](https://open.feishu.cn) 手动创建应用，填入凭证：

```python
from clawhermes_lark import run_setup_manual_flow
result = await run_setup_manual_flow(app_id="cli_xxx", app_secret="xxx")
```

## API 参考

### LarkAdapter

| 方法 | 说明 |
|:--|:--|
| `start()` / `stop()` | 启动/停止连接 |
| `send_response(resp, orig)` | 发送回复 (card/text/post) |
| `send_image(id, data)` / `send_file(id, data, name)` | 发送媒体 |
| `get_user_info(user_id)` | 获取用户信息 |
| `handle_webhook(body)` | Webhook 回调处理 |

### LarkClient

| 方法 | 说明 |
|:--|:--|
| `from_credentials(app_id, app_secret, domain)` | 从凭证创建 |
| `from_account(account)` | 从 LarkAccount 创建 (含缓存) |
| `probe()` | 连接探测 |
| `get_bot_identity()` | 获取 Bot 身份 |

### Channel — 消息管道

| 模块 | 函数/类 | 说明 |
|:--|:--|:--|
| `chat_queue` | `enqueue_feishu_chat_task()` | 串行队列入队 |
| `dedup` | `MessageDedup` | FIFO 去重 (TTL + sweep) |
| `abort_detect` | `is_abort_trigger()` / `is_likely_abort_text()` | 中止触发检测 |
| `targets` | `normalize_feishu_target()` | ID 规范化 |
| `interactive` | `InteractiveDispatcher` | 卡片回调分发 |
| `directory` | `list_feishu_directory_peers()` | 用户/群组查询 |

### Card — 卡片系统

| 模块 | 函数/类 |
|:--|:--|
| `builder` | `build_card()`, `build_streaming_card()`, `split_reasoning_text()` |
| `streaming` | `StreamingCardController` |
| `reply` | `create_reply_dispatcher()` |
| `tool_use` | `build_tool_use_display()` |
| `cardkit` | `create_card_entity()`, `update_card()` |
| `markdown_style` | `optimize_markdown_style()`, `strip_markdown()` |

### Core — 核心

| 模块 | 说明 |
|:--|:--|
| `accounts` | 多账户解析与管理 |
| `security` | 安全检查与警告收集 |
| `device_flow` | OAuth 2.0 用户授权 (RFC 8628) |
| `app_registration` | 扫码创建飞书应用 |
| `lark_ticket` | 请求追踪票据 |
| `token_store` | Token 持久化 |
| `scope_manager` | Scope 权限管理 |
| `tool_scopes` | 工具 Scope 定义 |

### OAPI Tools — 20 个飞书 API 工具

| 类别 | 函数 |
|:--|:--|
| Sheets | `sheets_get_meta`, `sheets_read_values`, `sheets_write_values`, `sheets_list` |
| Calendar | `calendar_list`, `calendar_list_events`, `calendar_create_event` |
| Drive | `drive_list_files`, `drive_download_file`, `drive_search` |
| Wiki | `wiki_list_spaces`, `wiki_get_node`, `wiki_list_nodes` |
| Docs | `docs_get_content`, `docs_get_meta` |
| IM/Chat | `im_get_chat_info`, `im_list_chat_members` |
| Common | `common_get_user`, `common_search_users` |
| Search | `search_enterprise` |

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

## 事件处理

| 事件类型 | 处理方式 |
|:--|:--|
| `im.message.receive_v1` | 管道 → 消息分发 |
| `im.message.read_v1` | 日志 |
| `im.message.reaction.created_v1` | synthetic event (own/all) |
| `im.message.reaction.deleted_v1` | 日志 |
| `im.chat.member.bot.added_v1` | 日志 |
| `im.chat.member.bot.deleted_v1` | 日志 |
| `card.action.trigger` | InteractiveDispatcher |
| `vc.bot.meeting_invited_v1` | 日志 (stub) |
| `drive.notice.comment_add_v1` | 日志 (stub) |
| `url_verification` | Challenge (Webhook) |

## 对齐状态

| 功能 | openclaw-lark | clawhermes-lark |
|:--|:--|:--|
| SDK Client + 多账户 | ✅ | ✅ |
| WebSocket + Webhook | ✅ | ✅ |
| 消息去重 + 串行队列 | ✅ | ✅ |
| 中止快速通道 | ✅ | ✅ |
| Reactions + 事件路由 | ✅ | ✅ |
| Typing 指示 + 消息编辑 | ✅ | ✅ |
| Card 消息 + 流式卡片 | ✅ | ✅ |
| 交互式卡片分发 | ✅ | ✅ |
| 工具调用显示 | ✅ | ✅ |
| Reasoning 分离 + Markdown 优化 | ✅ | ✅ |
| 群聊策略 + @提及 + 多账户 | ✅ | ✅ |
| 安全检查 + ID 规范化 | ✅ | ✅ |
| Flush 节流 + 错误处理 | ✅ | ✅ |
| OAPI 工具 (20) | ✅ | ✅ |
| 扫码创建飞书应用 | ✅ | ✅ |
| OAuth Device Flow (用户授权) | ✅ | ✅ |
| Token 存储 + Scope 管理 | ✅ | ✅ |
| 表单提问 + 消息格式转换 | ✅ | ✅ |
| 消息查询 + 目录查询 | ✅ | ✅ |
| 请求追踪 + 工具辅助 | ✅ | ✅ |
| CardKit + 不可用检测 | ✅ | ✅ |
| Onboarding + 配置向导 | ✅ | ✅ |
| VC Meeting + Drive Comment | ✅ (stub) | ✅ (stub) |

## 许可

MIT — 同 ClawHermes 项目
