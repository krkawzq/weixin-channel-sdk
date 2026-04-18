# weixin-channel-sdk 0.2.0 设计与实现计划

本文档基于对两个项目的复盘：

- 当前项目：`weixin-channel-sdk`，异步、可观测、可并发的开发者 SDK。
- 参考项目：`weixin-ilink`，同步、装饰器友好、开箱易用的 iLink SDK。

目标是在 0.2.0 中全面吸收 `weixin-ilink` 的优点，同时保留当前 SDK 的异步、可观测、可配置和长期运行能力。

更细的工程审计、优先级和验收标准见
[`docs/optimization-audit-0.2.0.md`](optimization-audit-0.2.0.md)。

## 1. 0.2.0 总目标

0.2.0 要成为一个更完整的“开发者友好型”微信 iLink SDK：

- 继续保持包名 `weixin-channel-sdk`，导入名 `weixin_channel`。
- 保留当前异步核心 `WeixinClient`。
- 增加类似 `weixin-ilink` 的高层 API：
  - 一行扫码登录。
  - 装饰器注册 handler。
  - 每条入站消息提供 `reply_*`、`download()`、`save()` 等快捷方法。
  - 支持同步和异步使用。
- 补齐关键协议细节：
  - `iLink-App-Id`
  - `iLink-App-ClientVersion`
  - 登录 `scaned_but_redirect`
  - CDN `upload_full_url`
- 增强内容处理：
  - 微信 emoji 标签转换。
  - 更精细 Markdown 过滤。
  - 流式 Markdown 过滤器。
- 保持纯 SDK，不引入 OpenClaw runtime 耦合。

## 2. 当前 SDK 的优点

当前项目相比 `weixin-ilink` 已具备这些优势：

- 异步 `httpx`，适合接 Claude/Codex/服务端。
- HTTP 连接池。
- `WeixinBot` 事件回调，可用于 TUI/Web/trace。
- `JsonlEventWriter` 和脱敏工具。
- `AccessPolicy` / `RateLimit`。
- `ConcurrencyConfig`，支持 `serial`、`per-conversation`、`concurrent`。
- 持久 session/cursor/seen/pause 状态。
- `SessionGuardConfig`，session 过期后持久暂停。
- `MediaConfig`，媒体大小限制、自动缩略图。
- 同步 wrapper。
- CLI login entrypoint。
- PyPI 已发布为 `weixin-channel-sdk`。

这些能力要保留，0.2.0 不应退回到单纯同步 SDK。

## 3. 当前 SDK 的缺点

从 `weixin-ilink` 对比看，当前缺点主要是：

1. 高层 API 不够顺手  
   当前 handler 收到的是整条 `WeixinMessage`，回复需要调用 `client.reply_text(msg, ...)`。  
   `weixin-ilink` 的 `IncomingMessage` 更自然：

   ```python
   @bot.on_text
   def handle(msg):
       msg.reply_text("hello")
   ```

2. Message item 粒度不够细  
   一条微信消息可能包含多个 `item`，当前 SDK 以 message 为单位处理，开发者还要自己遍历。

3. 协议 header 不完整  
   当前缺少全请求统一：

   ```text
   iLink-App-Id: bot
   iLink-App-ClientVersion: <encoded version>
   ```

4. 登录状态缺 `scaned_but_redirect`  
   某些区域跳转可能需要 `redirect_host`。

5. CDN 上传缺 `upload_full_url` 优先路径  
   当前只用 `upload_param + cdn_base_url`。

6. 内容处理不够丰富  
   当前只有简单 Markdown-to-text，没有：
   - 微信表情标签转换。
   - streaming Markdown filter。
   - 中文 italic/bold 兼容策略。

7. 本地 ACL 还不够开发者友好  
   当前 `AccessPolicy` 是内存式。可增加独立 SDK 的 allowlist 文件。

## 4. 0.2.0 协议补齐计划

### 4.1 iLink App Headers

新增配置字段：

```python
class WeixinApi:
    ilink_app_id: str = "bot"
    ilink_client_version: int = encode_client_version("2.1.8")
```

所有 GET/POST 统一带：

```text
iLink-App-Id: bot
iLink-App-ClientVersion: 131336
```

`encode_client_version("2.1.8")` 规则：

```python
((major & 0xff) << 16) | ((minor & 0xff) << 8) | (patch & 0xff)
```

### 4.2 登录 redirect

`QrStatusResponse.status` 增加：

```python
"scaned_but_redirect"
```

并增加字段：

```python
redirect_host: str | None
```

登录循环中：

```python
if status.status == "scaned_but_redirect" and status.redirect_host:
    polling_base_url = f"https://{status.redirect_host}"
```

`LoginEvent` 增加：

```python
type="redirected"
message="QR login redirected"
```

### 4.3 CDN upload_full_url

`GetUploadUrlResponse` 增加：

```python
upload_full_url: str | None
thumb_upload_full_url: str | None
```

`upload_buffer_to_cdn()` 优先使用 full URL：

```python
if upload_full_url:
    url = upload_full_url
else:
    url = build_cdn_upload_url(...)
```

这样兼容 `weixin-ilink` 和服务端新返回形态。

### 4.4 Channel Version

当前使用 `1.0.2`。0.2.0 建议调整为 SDK 自身标识：

```python
DEFAULT_CHANNEL_VERSION = "weixin-channel-sdk/0.2.0"
```

保留可配置，避免服务器严格校验时无法回退：

```python
WeixinApi(channel_version="1.0.2")
```

## 5. 0.2.0 高层 API 设计

### 5.1 新增 `IncomingMessage`

新增模块：

```text
src/weixin_channel/incoming.py
```

设计：

```python
@dataclass(slots=True)
class IncomingMessage:
    client: WeixinClient
    raw_message: WeixinMessage
    item: MessageItem

    @property
    def from_user(self) -> str: ...

    @property
    def context_token(self) -> str: ...

    @property
    def is_text(self) -> bool: ...
    @property
    def is_image(self) -> bool: ...
    @property
    def is_voice(self) -> bool: ...
    @property
    def is_file(self) -> bool: ...
    @property
    def is_video(self) -> bool: ...

    @property
    def text(self) -> str | None: ...
    @property
    def file_name(self) -> str | None: ...
    @property
    def voice_duration_ms(self) -> int: ...

    async def reply_text(self, text: str) -> str: ...
    async def reply_markdown(self, markdown: str) -> str: ...
    async def reply_image(self, path: str | Path, caption: str | None = None) -> str: ...
    async def reply_video(self, path: str | Path, caption: str | None = None) -> str: ...
    async def reply_file(self, path: str | Path, caption: str | None = None) -> str: ...
    async def reply_remote_media(self, url: str, caption: str | None = None) -> str: ...
    async def reply_typing(self) -> None: ...

    async def download(self, dest_dir: str | Path | None = None) -> DownloadedMedia | bytes | None: ...
    async def save(self, path: str | Path) -> Path: ...
```

### 5.2 `WeixinClient.incoming_messages()`

当前：

```python
async for msg in client.poll_messages():
    ...
```

新增：

```python
async for msg in client.incoming_messages():
    ...
```

它会把每个 `WeixinMessage.item_list` 拆成多个 `IncomingMessage`。

保留 `poll_messages()`，作为低层 API。

### 5.3 高层 Bot 装饰器

当前 `WeixinBot` 的 handler 接收 `WeixinMessage`。0.2.0 改为默认接收 `IncomingMessage`，但提供兼容方式：

```python
bot = WeixinBot(client, item_level=True)  # default True in 0.2.0

@bot.on_text
async def handle(msg: IncomingMessage):
    await msg.reply_text(f"echo: {msg.text}")

@bot.on_image
async def handle_image(msg):
    saved = await msg.save("inbox/pic.jpg")
    await msg.reply_text(f"saved: {saved}")

@bot.on_message
async def all_items(msg):
    ...
```

为了兼容当前 API，可提供：

```python
@bot.on_raw_message
async def raw(msg: WeixinMessage):
    ...
```

或构造参数：

```python
WeixinBot(client, item_level=False)
```

### 5.4 构造便利方法

吸收 `weixin-ilink` 命名：

```python
bot = await AsyncWeixinBot.from_login(save_to="creds.json")
bot = AsyncWeixinBot(credentials=info_dict)
bot = AsyncWeixinBot(credentials_file="creds.json")
```

当前 `WeixinClient.login()` 和 `StateStore` 继续保留。  
`save_to` 是一种更脚本友好的路径。

## 6. 内容处理设计

### 6.1 Emoji

新增：

```text
src/weixin_channel/emoji.py
```

API：

```python
translate_emoji(text: str) -> str
detag_emoji(text: str) -> str
known_emoji_tags() -> list[str]
```

`WeixinClient` 新增配置：

```python
emoji: EmojiConfig | None
```

或简单参数：

```python
WeixinClient(..., auto_emoji=True)
```

发送文本时顺序：

```text
markdown filter -> emoji translate -> chunk -> send
```

### 6.2 Streaming Markdown

新增：

```text
src/weixin_channel/markdown.py
```

API：

```python
filter_markdown(text: str) -> str
StreamingMarkdownFilter.feed(delta: str) -> str
StreamingMarkdownFilter.flush() -> str
```

保留现有 `markdown_to_plain_text()`，但作为简化 API。  
0.2.0 中 `reply_markdown()` 使用 `filter_markdown()`。

### 6.3 流式回复设计

为了接 LLM stream：

```python
async with msg.stream_reply() as reply:
    async for delta in llm_stream:
        await reply.feed(delta)
```

第一版可不立刻发送每个 delta，而是 buffer + 定时 flush：

```python
StreamReply(flush_interval_s=1.0, max_chunk_chars=1000)
```

这样避免微信端频繁刷屏。

## 7. 本地 ACL 设计

新增纯 SDK ACL，不使用 OpenClaw 文件路径：

```text
~/.weixin-channel/accounts/<account>.allow.json
```

模块：

```text
src/weixin_channel/acl.py
```

API：

```python
class AllowList:
    def read(account_id: str) -> set[str]
    def add(account_id: str, user_id: str) -> bool
    def remove(account_id: str, user_id: str) -> bool
    def contains(account_id: str, user_id: str) -> bool
```

和 `AccessPolicy` 集成：

```python
AccessPolicy.from_store(store, account_id)
```

CLI 增加：

```bash
weixin-channel allow add <user_id>
weixin-channel allow remove <user_id>
weixin-channel allow list
```

## 8. CLI 0.2.0 设计

当前只有：

```bash
weixin-channel login
```

0.2.0 增加：

```bash
weixin-channel accounts
weixin-channel whoami
weixin-channel allow add <user_id>
weixin-channel allow remove <user_id>
weixin-channel allow list
weixin-channel echo
```

`echo` 用于快速验收：

```bash
weixin-channel echo --allow-from <user_id>
```

## 9. 测试计划

### 9.1 单元测试

新增覆盖：

- `encode_client_version("2.1.8")`
- headers 全量包含 `iLink-App-*`
- `scaned_but_redirect`
- `upload_full_url`
- emoji translate/detag
- streaming markdown filter
- incoming item split
- `IncomingMessage.reply_*`
- ACL store
- CLI parser

### 9.2 MockTransport 集成测试

用 `httpx.MockTransport` 模拟：

- QR login success
- QR login redirect
- getupdates 一条多 item 消息
- send text
- upload full URL media

### 9.3 Live 测试

最后用真实账号验证：

- login
- echo
- text
- image receive
- file receive
- image send
- file send
- typing

## 10. 兼容策略

0.2.0 尽量不破坏 0.1.0：

- `WeixinClient` 保持。
- `WeixinBot.on_text` 等方法保留，但默认 handler 参数可能从 `WeixinMessage` 改为 `IncomingMessage`。这是潜在 breaking change。

建议采取温和过渡：

```python
WeixinBot(client, item_level=True)
```

默认 0.2.0 仍可设为 `False`，文档推荐 `True`。  
0.3.0 再考虑默认改成 item-level。

或者新增独立类：

```python
DevWeixinBot
```

但命名上不如 `WeixinBot(item_level=True)` 简洁。

## 11. 实施顺序

### Phase 1：协议补齐

1. `iLink-App-*` headers。
2. `scaned_but_redirect`。
3. `upload_full_url`。
4. tests。

### Phase 2：内容处理

1. `emoji.py`。
2. `markdown.py` + streaming filter。
3. `reply_markdown()`。
4. tests。

### Phase 3：高层 API

1. `IncomingMessage`。
2. `client.incoming_messages()`。
3. `WeixinBot(item_level=True)`。
4. `on_image/on_voice/on_file/on_video` decorator。
5. tests。

### Phase 4：CLI/ACL

1. `acl.py`。
2. CLI `accounts/whoami/allow/echo`。
3. tests。

### Phase 5：文档与发布

1. README 以高层 API 为主。
2. 保留低层 API 章节。
3. CHANGELOG 0.2.0。
4. 构建、twine check、发布。

## 12. 对我们的定位再确认

`weixin-ilink` 的 API 非常顺手，值得吸收；但我们的项目应继续保持差异化：

- async-first
- trace/event friendly
- concurrency friendly
- state management stronger
- CLI tool better
- suitable for Claude/Codex bridges and long-running agent services

0.2.0 的目标不是变成 `weixin-ilink` 的复制，而是把它的高层交互体验吸收到我们的 async SDK 中。
