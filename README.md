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
├── __init__.py        # 公共 API 导出
├── client.py          # LarkClient — 统一 SDK 管理器
│   ├── from_credentials()  # 凭证 → 客户端
│   ├── probe()             # 连接探测 (bot/v3/info)
│   └── get_bot_identity()  # 机器人身份获取
├── adapter.py         # LarkAdapter — ChannelAdapter 实现
│   ├── WebSocket 长连接 + 自动重连
│   ├── HTTP Webhook 消息回调
│   ├── 消息去重 (LRU) + 按 chat 串行锁
│   └── 权限门控 (group_policy / allowlist / @提及)
├── messaging.py       # 消息交互增强 (对齐 openclaw-lark)
│   ├── Reactions (添加 / 移除 / 列出)
│   ├── TypingIndicator (模拟"正在输入")
│   ├── edit_message (编辑已发送消息)
│   └── Card 消息 (build_markdown_card / send_card)
└── hermes_vendor/     # Hermes 生产级消息引擎 (5,512 行)
    ├── feishu_hermes.py   # 消息解析 / Markdown 转换 / @提及
    └── _compat.py         # 向后兼容层
```

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
)
adapter = LarkAdapter(config)
await adapter.connect()

# 3. 消息交互 (新增)
from clawhermes_lark import TypingIndicator, add_reaction

async with TypingIndicator(client, message_id):
    await process_request()  # 自动显示"正在输入"
```

## API 参考

### LarkClient

| 方法 | 说明 |
|:--|:--|
| `from_credentials(app_id, app_secret, domain)` | 从凭证创建客户端 |
| `probe()` / `probe_sync()` | 连接探测 (bot/v3/info) |
| `get_bot_identity()` / `get_bot_identity_sync()` | 获取机器人身份 |
| `_resolve_domain(domain)` | 域名 → lark-oapi Domain 枚举 |

### LarkAdapter

| 方法 | 说明 |
|:--|:--|
| `connect()` | 启动连接 (WebSocket 或 Webhook) |
| `disconnect()` | 断开连接 |
| `send_response(response, original)` | 发送回复消息 |
| `send_message(content, target_id)` | 发送文本消息 |
| `send_image(image_key, target_id)` | 发送图片 |
| `send_file(file_key, file_type, target_id)` | 发送文件 |

### Messaging (新)

| 函数 | 说明 |
|:--|:--|
| `add_reaction(client, message_id, emoji_type)` | 添加表情回应 |
| `remove_reaction(client, message_id, reaction_id)` | 移除表情回应 |
| `list_reactions(client, message_id)` | 列出所有回应 |
| `edit_message(client, message_id, content)` | 编辑消息 |
| `build_markdown_card(text)` | 构建 Markdown 卡片 payload |
| `send_card(client, receive_id, card)` | 发送交互式卡片 |

### TypingIndicator

```python
typing = TypingIndicator(client, message_id)
await typing.start()   # 显示"正在输入"
await typing.stop()    # 移除

# 或使用上下文管理器
async with TypingIndicator(client, message_id):
    await process()
```

## 配置

```bash
# 环境变量
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export FEISHU_DOMAIN=feishu       # feishu | lark
export FEISHU_GROUP_POLICY=allowlist  # allowlist | open | disabled
```

## 对齐状态

| 功能 | larksuite/openclaw-lark | clawhermes-lark |
|:--|:--|:--|
| SDK Client | `LarkClient` + 多账户 | `LarkClient` ✅ |
| 连接探测 | `probeFeishu()` | `probe()` ✅ |
| Bot 身份 | bot/v3/info | `get_bot_identity()` ✅ |
| WebSocket | 长连接 + 自动重连 | ✅ |
| Webhook | HTTP 回调 + 签名验证 | ✅ |
| 消息去重 | LRU dedup | ✅ |
| Reactions | 添加/移除/列出 | ✅ (新增) |
| Typing 指示 | 时钟 emoji 模拟 | `TypingIndicator` ✅ (新增) |
| 消息编辑 | PATCH API | `edit_message()` ✅ (新增) |
| Card 消息 | Markdown card builder | `build_markdown_card()` + `send_card()` ✅ (新增) |
| 群聊策略 | allowlist/open/disabled | ✅ |
| @提及 | 精确匹配 + 回退 | ✅ |
| 多账户 | 支持 | 待实现 |
| OAPI 工具 | docs/calendar/sheets/... | 待实现 |

## 许可

MIT — 同 ClawHermes 项目
