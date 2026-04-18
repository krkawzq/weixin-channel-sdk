# weixin-channel-sdk 0.2.0 优化审计与执行计划

本文档是在 0.1.0 代码基础上，对 0.2.0 做的一次工程审计。关注三个方向：

1. 协议功能支持是否完整。
2. API 交互是否足够 Python developer friendly。
3. 性能、数据校验、长期运行 bug 风险是否可控。

结论：0.1.0 已经具备可用的 async-first SDK 骨架，但 0.2.0 应优先补齐协议兼容性和高层 API。性能优化要围绕长期运行、媒体文件、LLM stream 和多会话场景展开。

## 1. 当前状态判断

当前 SDK 已经覆盖：

- QR 登录。
- session/cursor/seen/pause 本地状态。
- `getupdates` 长轮询。
- 文本回复和分片发送。
- CDN 媒体上传、下载、AES-128-ECB 加解密。
- typing ticket 缓存。
- `WeixinBot` 装饰器式 handler。
- `AccessPolicy`、rate limit、群聊触发策略。
- runtime event、JSONL 日志、同步 wrapper。

当前 SDK 的主要短板：

- 协议头不完整，且 `get_qrcode_status` 仍使用硬编码 `iLink-App-ClientVersion: 1`。
- 登录流程未处理 `scaned_but_redirect`。
- CDN 上传未优先使用服务端返回的 `upload_full_url` / `thumb_upload_full_url`。
- 入站 CDN `full_url` 未建模，下载只使用 `encrypt_query_param + cdn_base_url`。
- handler 参数还是 message 级别，开发者要自己遍历 `item_list`。
- 回复 API 仍需要开发者理解 `context_token`、`to_user_id` 等协议概念。
- Markdown、emoji、LLM streaming 回复还比较粗糙。
- 媒体读写和远程下载存在一次性加载大文件的问题。
- 部分配置缺少运行前校验，bug 会延迟到运行中暴露。

## 2. 0.2.0 优先级

### P0：协议正确性

P0 是发布 0.2.0 前必须完成的内容。否则 SDK 看起来方便，但在真实账号、不同 IDC、不同 CDN 返回形态下会不稳定。

任务：

- 统一 iLink headers。
- 支持扫码 redirect。
- 支持 CDN full URL 上传和下载。
- 增强模型字段和基础校验。
- 修复 getupdates 错误响应处理。
- 增加协议级 mock 测试。

验收标准：

- 所有 GET/POST 请求都带 `iLink-App-Id` 和 `iLink-App-ClientVersion`。
- QR 登录 mock 能覆盖 `wait -> scaned -> scaned_but_redirect -> confirmed`。
- 媒体上传优先使用 `upload_full_url`，无 full URL 时回退到 `upload_param + cdn_base_url`。
- 媒体下载优先使用 `full_url`，无 full URL 时回退到 `encrypt_query_param + cdn_base_url`。
- Pydantic 模型能接住已知字段，不因未知字段崩溃。

### P1：高层 API

P1 是 0.2.0 的核心用户价值。目标不是复制 `weixin-ilink`，而是在 async SDK 上吸收它的顺手体验。

任务：

- 新增 item 级 `IncomingMessage`。
- 新增 `WeixinClient.incoming_messages()`。
- 扩展 `WeixinBot` 装饰器：`on_image`、`on_voice`、`on_file`、`on_video`、`on_raw_message`。
- 新增 `reply_*`、`download()`、`save()`、`stream_reply()`。
- 新增 credentials/info_json 便利构造。
- README 示例以高层 API 为主，低层 API 保留为高级用法。

验收标准：

- 新用户可以用 15 行以内写出 echo bot。
- handler 中不需要手动传 `to_user_id` 或 `context_token`。
- 老的 `WeixinClient.poll_messages()` 和 message 级 handler 保持兼容。
- async 和 sync 两种使用方式都有示例。

### P2：性能与长期运行

P2 不一定阻塞 0.2.0 首个 beta，但应尽量合入正式 0.2.0。

任务：

- CDN 上传/下载复用 HTTP client。
- 大文件读写和远程下载避免阻塞 event loop。
- 远程下载按 chunk 限制 `max_bytes`，不要下载完再判断。
- seen-id 去重从排序截断改为 LRU 语义。
- `RateLimit` 和 conversation lock 做容量控制。
- retry/backoff 增加 jitter，降低多 bot 同时恢复时的尖峰。
- handler task 异常进入 event/error hook，不静默吞掉。

验收标准：

- 单次 100MB 媒体路径不额外复制多份长期驻留内存。
- 并发模式下 task 异常可观测。
- 长期运行时 `_conversation_locks`、rate limit map、seen set 不无界增长。
- media helper 不为每个文件创建新连接池。

## 3. 协议功能支持审计

### 3.1 Headers

当前问题：

- `_headers()` 只覆盖 POST 场景。
- `get_bot_qrcode()` 只传 `SKRouteTag`。
- `get_qrcode_status()` 传了硬编码 `iLink-App-ClientVersion: 1`。
- `iLink-App-Id` 完全缺失。

0.2.0 设计：

```python
DEFAULT_ILINK_APP_ID = "bot"
DEFAULT_ILINK_CLIENT_VERSION_STR = "2.1.8"

def encode_client_version(version: str) -> int:
    ...
```

`WeixinApi` 增加：

```python
ilink_app_id: str = DEFAULT_ILINK_APP_ID
ilink_client_version: int = encode_client_version(DEFAULT_ILINK_CLIENT_VERSION_STR)
```

新增内部方法：

```python
def _common_headers(self) -> dict[str, str]:
    return {
        "iLink-App-Id": self.ilink_app_id,
        "iLink-App-ClientVersion": str(self.ilink_client_version),
        ...
    }
```

所有 GET/POST 都复用 `_common_headers()`。

### 3.2 QR 登录 redirect

当前问题：

- `QrStatusResponse.status` 只允许 `wait | scaned | confirmed | expired`。
- 未建模 `redirect_host`。
- 登录循环无法切换 polling base URL。

0.2.0 设计：

```python
class QrStatusResponse(_Model):
    status: Literal["wait", "scaned", "scaned_but_redirect", "confirmed", "expired"]
    redirect_host: str | None = None
```

登录循环：

- `scaned`：继续等待。
- `scaned_but_redirect`：如果有 `redirect_host`，后续 status polling 切到 `https://<redirect_host>`。
- `expired`：刷新二维码，并把 polling base URL 重置为原 base URL。
- `confirmed`：保存 `bot_token`、`ilink_bot_id`、`ilink_user_id`、`baseurl`。

事件设计：

- 保留服务端拼写 `scaned`，避免使用者对协议值困惑。
- 额外 yield `LoginEvent("redirected", ...)`，便于 CLI/TUI 显示。

### 3.3 CDN full URL

当前问题：

- `GetUploadUrlResponse` 只有 `upload_param` 和 `thumb_upload_param`。
- `upload_buffer_to_cdn()` 必须自己拼 `cdn_base_url/upload?...`。
- 入站媒体模型没有 `full_url` 字段，下载不能优先走服务端返回 URL。

0.2.0 设计：

```python
class CDNMedia(_Model):
    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None
    full_url: str | None = None

class GetUploadUrlResponse(_Model):
    upload_param: str | None = None
    upload_full_url: str | None = None
    thumb_upload_param: str | None = None
    thumb_upload_full_url: str | None = None
```

上传 URL 选择：

1. `upload_full_url`
2. `upload_param + cdn_base_url`
3. 抛出协议错误

下载 URL 选择：

1. `media.full_url`
2. `media.encrypt_query_param + cdn_base_url`
3. 返回 `None` 或抛出明确错误，取决于调用方 API

### 3.4 Media item 覆盖

当前支持：

- TEXT
- IMAGE
- VOICE 入站下载和 ASR 文本抽取
- FILE
- VIDEO

待补齐：

- 出站 `send_image`、`send_video`、`send_file`、`send_voice` 语义化方法。
- voice encode metadata：`encode_type`、`sample_rate`、`bits_per_sample`、`playtime`。
- 图片和视频的宽高、缩略图宽高字段，能获取就填，获取不到不阻塞。
- caption 发送策略明确化。

建议：

- 继续保留当前“caption 和 media 在同一个 message 的 item_list”实现。
- 同时提供 `caption_mode="same-message" | "separate-message"`，默认 `same-message`。
- 如果真实测试发现微信客户端对某类媒体不显示 caption，再针对类型调整默认值。

### 3.5 Error handling

当前情况：

- `_post_json()` 能识别 `SESSION_EXPIRED_ERRCODE = -14`。
- 普通 POST 非 0 `ret/errcode` 会抛错。
- `getupdates` 使用 `raise_on_ret_error=False`，除了 session expired 外，其它错误会进入模型。

0.2.0 计划：

- `GetUpdatesResponse` 增加：

```python
def is_ok(self) -> bool: ...
def error_code(self) -> int | None: ...
```

- `poll_messages()` 明确处理非 0 错误：
  - session expired：暂停并抛 `WeixinSessionExpired`。
  - transient ret：进入 retry/backoff。
  - unknown ret：emit `poll.error` event。

## 4. API 交互与高层接口

### 4.1 IncomingMessage

当前 `WeixinBot` handler 接收 `WeixinMessage`，对脚本开发不够顺手。

0.2.0 新增 `IncomingMessage`：

```python
@dataclass(slots=True)
class IncomingMessage:
    client: WeixinClient
    raw_message: WeixinMessage
    item: MessageItem

    @property
    def text(self) -> str | None: ...
    @property
    def from_user(self) -> str: ...
    @property
    def conversation_id(self) -> str: ...

    async def reply_text(self, text: str) -> str: ...
    async def reply_markdown(self, markdown: str) -> str: ...
    async def reply_image(self, path: str | Path, *, caption: str | None = None) -> str: ...
    async def reply_video(self, path: str | Path, *, caption: str | None = None) -> str: ...
    async def reply_file(self, path: str | Path, *, caption: str | None = None) -> str: ...
    async def reply_remote_media(self, url: str, *, caption: str | None = None) -> str: ...
    async def download(self, dest_dir: str | Path | None = None) -> DownloadedMedia | None: ...
    async def save(self, path: str | Path) -> Path: ...
```

重点：

- `context_token` 不再暴露为日常必填参数。
- `raw_message` 和 `item` 保留，给高级用户处理边缘协议字段。
- 发送失败抛 SDK 自定义异常，不抛裸 `RuntimeError`。

### 4.2 Bot 装饰器

当前：

```python
@bot.on_text
async def handle(msg: WeixinMessage):
    return f"echo: {msg.text()}"
```

0.2.0 推荐：

```python
@bot.on_text
async def handle(msg: IncomingMessage):
    await msg.reply_text(f"echo: {msg.text}")
```

新增：

```python
@bot.on_image
@bot.on_voice
@bot.on_file
@bot.on_video
@bot.on_raw_message
@bot.on_command("/help")
```

兼容策略：

- 0.2.0 保持 `WeixinBot(client, item_level=False)` 兼容老行为。
- 文档和新示例使用 `item_level=True`。
- 0.3.0 再考虑默认切到 item-level。

### 4.3 Credentials 便利构造

吸收 `weixin-ilink` 的优点，但不放弃 `StateStore`。

新增：

```python
client = await WeixinClient.from_login(save_to="creds.json")
client = WeixinClient.from_credentials(info)
client = WeixinClient.from_credentials_file("creds.json")
info = client.to_credentials()
```

`StateStore` 继续作为默认长期运行状态层。`credentials_file` 面向一次性脚本，`StateStore` 面向长期服务。

### 4.4 Stream Reply

LLM 集成需要降低“流式 token 直接刷屏”的风险。

设计：

```python
async with msg.stream_reply(flush_interval_s=1.0, max_chunk_chars=1000) as reply:
    async for delta in llm_stream:
        await reply.feed(delta)
```

策略：

- 内部用 `StreamingMarkdownFilter` 清理 markdown。
- 按时间和字符数批量 flush。
- 最后 `flush()` 保证尾部内容发送。
- 初版只追加发送新消息，不做编辑，因为当前协议没有稳定的 bot message update 能力。

### 4.5 CLI

当前 CLI 只有 `login`。

0.2.0 增加：

```bash
weixin-channel accounts
weixin-channel whoami
weixin-channel logout
weixin-channel allow add <user_id>
weixin-channel allow remove <user_id>
weixin-channel allow list
weixin-channel echo
```

CLI 不应该变成 runtime 框架，只提供开发、调试和验收能力。

## 5. 性能、校验、bug 风险

### 5.1 HTTP client 复用

当前问题：

- `WeixinApi` 自身复用 `httpx.AsyncClient`。
- CDN helper 在没有传 `http_client` 时会临时创建新 client。
- `WeixinClient.send_media_file()` 没有把共享 client 传入 CDN helper。

计划：

- `WeixinClient` 增加内部 media HTTP client，或允许 `WeixinApi` 暴露同一个 `AsyncClient`。
- CDN upload/download 和 remote download 默认复用 SDK 级连接池。
- `close()` 统一关闭。

验收：

- 连续发送多个文件不会每个文件创建一个连接池。
- 单元测试可通过 fake transport 验证同一 client 被复用。

### 5.2 文件与下载流式化

当前问题：

- `Path.read_bytes()`、`Path.write_bytes()` 在 async 路径中阻塞 event loop。
- `download_remote_file()` 下载完整响应后才判断 `max_bytes`。
- CDN download 同样一次性读取响应。

计划：

- 文件读写使用 `asyncio.to_thread()` 包装，短期先避免阻塞 event loop。
- 远程下载使用 `client.stream()` 和 `aiter_bytes()`，边读边累计大小。
- CDN 下载也按 chunk 限制最大字节。
- AES-ECB 加密初版仍可整块处理；后续再考虑真正 streaming cipher。

验收：

- 超过 `max_download_bytes` 的远程文件在超限时中断，不会下载完整文件。
- 大文件 I/O 不直接阻塞 event loop。

### 5.3 Dedupe 语义

当前问题：

- `flush_seen_message_ids()` 用 `sorted(self._seen_message_ids)[-1000:]` 截断。
- 如果 message_id 不是严格递增，可能保留“数值最大”而不是“最近处理”。

计划：

- 使用 `collections.OrderedDict[int, None]` 或自定义 LRU。
- `seen_limit` 放入 `RetryConfig` 或新 `DedupeConfig`。

验收：

- 单元测试覆盖乱序 message_id，保留最近处理的 N 个。

### 5.4 并发资源清理

当前问题：

- `_conversation_locks` 只增不减。
- `RateLimit._events` 只在 key 再次访问时清理。
- `_tasks` 异常如果脱离 `_process_message`，可能只在 `_wait_for_one_task()` 中被 suppress。

计划：

- conversation locks 增加 LRU 上限或 idle TTL。
- `RateLimit` 增加 `max_keys`，超出时清理最旧 key。
- task done callback 中读取 `task.exception()` 并 emit `task.error`。
- `BotEvent` 中加入 task id / conversation id / handler kind。

验收：

- 压测不同 conversation 后，lock map 不超过上限。
- handler 抛异常时，`on_error` 和 `on_event` 都能观测到。

### 5.5 配置和输入校验

当前问题：

- `ConcurrencyConfig.mode` 是裸字符串。
- `timeout`、`max_upload_bytes`、`seen_flush_interval` 可被设为不合理值。
- `ensure_base_url()` 只做 `rstrip("/")`，不校验 scheme/host。
- `send_text()` 按 Python 字符数切分，未考虑字节长度和 emoji grapheme。

计划：

- dataclass 增加 `__post_init__()`：
  - `mode in {"serial", "per-conversation", "concurrent"}`
  - timeout > 0
  - max bytes >= 0
  - concurrency >= 1
- `ensure_base_url()` 校验 `http/https` 和 netloc。
- `_chunk_text()` 改为 byte-aware，避免超过服务端限制。
- 对空文本、空文件路径、缺失 session、缺失 context_token 给出 SDK 自定义异常。

验收：

- 错误配置在构造阶段失败。
- 用户看到的是 `WeixinConfigError` / `WeixinProtocolError`，不是底层 `ValueError` 或 `RuntimeError`。

### 5.6 文本处理

当前问题：

- `markdown_to_plain_text()` 是一次性正则，能用但不适合 LLM streaming。
- 缺少微信 emoji 标签转换。

计划：

- 新增 `emoji.py`：
  - `translate_emoji(text)`
  - `detag_emoji(text)`
  - `known_emoji_tags()`
- 新增 `markdown.py`：
  - `filter_markdown(text)`
  - `StreamingMarkdownFilter`
- `reply_markdown()` 默认走 markdown filter，再走 emoji translate。

验收：

- `[微笑]` 等标签能转成 Unicode emoji。
- LLM delta 分片不会在 `**bold`、代码块、图片 markdown 等边界上输出异常残片。

## 6. 0.2.0 实施拆分

### Phase A：协议补齐

文件：

- `src/weixin_channel/api.py`
- `src/weixin_channel/models.py`
- `src/weixin_channel/cdn.py`
- `src/weixin_channel/client.py`
- `tests/test_core.py`

任务：

1. 新增 `encode_client_version()` 和 iLink header 常量。
2. 所有 GET/POST 使用 `_common_headers()`。
3. `QrStatusResponse` 支持 `scaned_but_redirect` / `redirect_host`。
4. 登录循环支持 polling base URL redirect。
5. `GetUploadUrlResponse` 支持 full URL。
6. `CDNMedia` 支持 `full_url`。
7. upload/download URL 优先 full URL。

### Phase B：高层消息 API

文件：

- `src/weixin_channel/incoming.py`
- `src/weixin_channel/client.py`
- `src/weixin_channel/bot.py`
- `src/weixin_channel/sync.py`
- `src/weixin_channel/__init__.py`
- `examples/echo_bot.py`

任务：

1. `IncomingMessage` typed wrapper。
2. `WeixinClient.incoming_messages()`。
3. `IncomingMessage.reply_*` / `download()` / `save()`。
4. `WeixinBot(item_level=True)`。
5. item type decorators。
6. 保留 raw message 兼容模式。

### Phase C：内容与 LLM 友好接口

文件：

- `src/weixin_channel/emoji.py`
- `src/weixin_channel/markdown.py`
- `src/weixin_channel/client.py`
- `src/weixin_channel/incoming.py`
- `examples/claude_bridge.py`

任务：

1. emoji translate/detag。
2. streaming markdown filter。
3. `reply_markdown()`。
4. `stream_reply()`。

### Phase D：性能与稳定性

文件：

- `src/weixin_channel/cdn.py`
- `src/weixin_channel/client.py`
- `src/weixin_channel/policy.py`
- `src/weixin_channel/config.py`
- `src/weixin_channel/utils.py`
- `src/weixin_channel/bot.py`

任务：

1. shared media HTTP client。
2. remote/CDN download streaming。
3. async file I/O wrapper。
4. LRU seen dedupe。
5. lock/rate-limit 容量控制。
6. config validators。
7. task exception observability。

### Phase E：CLI 与文档

文件：

- `src/weixin_channel/acl.py`
- `src/weixin_channel/cli.py`
- `README.md`
- `CHANGELOG.md`
- `docs/*.md`
- `examples/*.py`

任务：

1. `accounts` / `whoami` / `logout`。
2. allowlist store 和 CLI。
3. `echo` 验收命令。
4. README 主示例改为 item-level API。
5. CHANGELOG 0.2.0。

## 7. 测试矩阵

必须新增：

- `encode_client_version("2.1.8") == 131336`
- GET/POST headers 都有 `iLink-App-*`
- QR redirect login
- `upload_full_url` upload path
- CDN `full_url` download path
- `IncomingMessage` item split
- `IncomingMessage.reply_text()`
- item decorators dispatch
- markdown streaming filter
- emoji translate/detag
- config validation
- LRU seen dedupe
- remote download max bytes streaming abort
- CLI parser for accounts/whoami/allow/echo

建议新增 live checklist，不放入自动 CI：

- `weixin-channel login`
- `weixin-channel whoami`
- `weixin-channel echo`
- 收文本
- 收图片
- 收文件
- 发文本
- 发图片
- 发文件
- typing
- session 过期后的暂停行为

## 8. 版本边界

0.2.0 应该包含：

- 协议 parity。
- 高层 item-level API。
- Markdown/emoji。
- 基础 stream reply。
- 性能和校验关键修复。
- CLI 调试命令。

0.2.0 不建议包含：

- OpenClaw runtime 耦合。
- 微信桌面客户端逆向注入。
- Web/TUI dashboard。
- 完整 voice 编码依赖强制安装。
- 消息编辑或撤回能力，除非协议已验证稳定。

## 9. 推荐执行顺序

最稳的推进顺序：

1. Phase A：先补协议，确保底座正确。
2. Phase B：再做高层 API，避免在错误协议抽象上封装。
3. Phase C：接 LLM 场景，把 markdown/emoji/stream 做顺。
4. Phase D：压长期运行和媒体路径。
5. Phase E：最后补 CLI、README、CHANGELOG，准备 0.2.0 发布。

每个 phase 都应该以测试收口，不等到最后一次性补测试。真实账号测试仍留到发布前最后阶段。
