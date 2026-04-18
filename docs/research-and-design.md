# Weixin Channel SDK 调研与设计

本文档整理 `forks/openclaw-weixin` 的协议与实现细节，并说明如何基于它拆出一个可自定义的 Python SDK。

## 1. 目标

我们希望得到一个独立 Python SDK，使 Python 程序可以直接接入微信 ClawBot/iLink 通道：

- 扫码登录微信 ClawBot。
- 长轮询接收微信消息。
- 调用自己的工具、Claude Agent SDK、Codex SDK 或任意业务后端。
- 将结果回发到微信。
- 不依赖 OpenClaw runtime，但保留将来接回 OpenClaw 的可能。

首版目标只做文本链路。媒体、CDN、语音转码、OpenClaw routing、pairing store 等放在后续版本。

## 2. 原始包结构

`@tencent-weixin/openclaw-weixin` 主要分层如下：

- `index.ts`：OpenClaw 插件入口，注册 channel 和 CLI。
- `src/auth/login-qr.ts`：二维码登录，轮询扫码状态，获取 `bot_token`。
- `src/auth/accounts.ts`：账号、token、baseUrl、cdnBaseUrl 的持久化和解析。
- `src/api/api.ts`：iLink HTTP JSON API 封装。
- `src/api/types.ts`：协议类型。
- `src/monitor/monitor.ts`：`getUpdates` 长轮询主循环。
- `src/messaging/inbound.ts`：入站消息转换和 `context_token` 缓存。
- `src/messaging/send.ts`：文本和媒体消息 payload 构造与发送。
- `src/messaging/process-message.ts`：OpenClaw 鉴权、路由、session、reply dispatcher。
- `src/cdn/*`：媒体 AES-128-ECB 加解密、CDN 上传/下载 URL。
- `src/media/*`：媒体保存、MIME、SILK 转 WAV。

Python SDK 首版只抽取 `login-qr.ts`、`api.ts`、`types.ts`、`monitor.ts`、`send.ts` 的必要子集。

## 3. 协议入口

默认 API base：

```text
https://ilinkai.weixin.qq.com
```

默认 CDN base：

```text
https://novac2c.cdn.weixin.qq.com/c2c
```

登录接口：

```text
GET /ilink/bot/get_bot_qrcode?bot_type=3
GET /ilink/bot/get_qrcode_status?qrcode=<qrcode>
```

消息接口：

```text
POST /ilink/bot/getupdates
POST /ilink/bot/sendmessage
POST /ilink/bot/getuploadurl
POST /ilink/bot/getconfig
POST /ilink/bot/sendtyping
```

所有 POST 请求都带：

```json
{
  "base_info": {
    "channel_version": "1.0.2"
  }
}
```

## 4. 请求头

原始包构造 POST header 的规则：

```text
Content-Type: application/json
Content-Length: <JSON body bytes>
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: base64(decimal random uint32)
SKRouteTag: <optional>
```

`X-WECHAT-UIN` 生成算法：

1. 随机 4 字节。
2. 按 big-endian 解析为 uint32。
3. 转成十进制字符串。
4. UTF-8 后 base64。

Python 实现必须保持这个细节。

## 5. 登录流程

`bot_type` 固定为 `3`。

流程：

1. `GET /ilink/bot/get_bot_qrcode?bot_type=3`
2. 服务端返回：
   - `qrcode`
   - `qrcode_img_content`
3. 用户扫码。
4. 轮询 `get_qrcode_status?qrcode=...`
5. 状态可能为：
   - `wait`
   - `scaned`
   - `expired`
   - `confirmed`
6. `confirmed` 时得到：
   - `bot_token`
   - `ilink_bot_id`
   - `baseurl`
   - `ilink_user_id`

SDK 应保存：

```json
{
  "token": "...",
  "base_url": "...",
  "account_id": "...@im.bot",
  "user_id": "...@im.wechat",
  "saved_at": "..."
}
```

token 文件应尽量设为 `0600`。

## 6. getUpdates 长轮询

请求：

```json
{
  "get_updates_buf": "<cursor or empty>",
  "base_info": {
    "channel_version": "1.0.2"
  }
}
```

响应：

```json
{
  "ret": 0,
  "msgs": [],
  "get_updates_buf": "<new cursor>",
  "longpolling_timeout_ms": 35000
}
```

关键点：

- `get_updates_buf` 是 cursor，必须持久化。
- 初次请求传空字符串。
- 每次成功响应后用新 cursor 覆盖旧 cursor。
- HTTP client 侧超时是长轮询正常现象，应返回空消息。
- `errcode == -14` 或 `ret == -14` 表示 session expired，应暂停并要求重新登录。
- 需要 message-id 去重，避免 cursor 失效或重启导致重复处理。

## 7. sendMessage 文本回复

文本消息 payload：

```json
{
  "msg": {
    "from_user_id": "",
    "to_user_id": "<from_user_id of inbound message>",
    "client_id": "weixin-channel:<timestamp>-<random>",
    "message_type": 2,
    "message_state": 2,
    "context_token": "<inbound context_token>",
    "item_list": [
      {
        "type": 1,
        "text_item": {
          "text": "reply text"
        }
      }
    ]
  },
  "base_info": {
    "channel_version": "1.0.2"
  }
}
```

`context_token` 是最大坑点。每条入站消息都会携带 `context_token`，回复必须原样带回。缺失时不应发送。

因此 Python SDK 提供：

```python
await client.reply_text(msg, "...")
```

让 SDK 自动使用：

- `to_user_id = msg.from_user_id`
- `context_token = msg.context_token`

## 8. 消息模型

必要常量：

```text
MessageType.USER = 1
MessageType.BOT = 2

MessageState.NEW = 0
MessageState.GENERATING = 1
MessageState.FINISH = 2

MessageItemType.TEXT = 1
MessageItemType.IMAGE = 2
MessageItemType.VOICE = 3
MessageItemType.FILE = 4
MessageItemType.VIDEO = 5
```

首版模型：

- `WeixinMessage`
- `MessageItem`
- `TextItem`
- `ImageItem`
- `VoiceItem`
- `FileItem`
- `VideoItem`
- `CDNMedia`
- `GetUpdatesResponse`
- `AccountSession`

`WeixinMessage.text()` 应支持：

- 文本消息：`text_item.text`
- 语音转文字：`voice_item.text`
- 图片/文件/视频占位描述

## 9. 媒体能力后续设计

媒体上传流程：

1. 读取文件。
2. 计算明文 MD5。
3. 生成随机 `filekey`。
4. 生成随机 16 字节 AES key。
5. 计算 AES-128-ECB + PKCS7 后的密文长度。
6. 调 `getuploadurl`。
7. AES-128-ECB 加密文件。
8. POST 到 CDN upload URL。
9. 从 response header `x-encrypted-param` 获取下载参数。
10. `sendmessage` 中填 `encrypt_query_param` 和 `aes_key`。

媒体下载流程：

1. 用 `encrypt_query_param` 构造 CDN download URL。
2. 下载密文。
3. 解析 `aes_key`。
4. AES-128-ECB 解密。
5. 保存本地文件。

`aes_key` 有两个编码形态：

- `base64(raw 16 bytes)`
- `base64(hex string of 16 bytes)`

Python SDK 后续必须兼容这两种。

## 10. OpenClaw 依赖剥离

不进入首版 SDK：

- OpenClaw `ChannelPlugin`
- `channelRuntime.reply.dispatchReplyFromConfig`
- OpenClaw session store
- pairing store
- routing
- OpenClaw media store
- logs-upload CLI

由 SDK 自己替代：

- token store
- cursor store
- allowlist
- callback runner
- simple retry/backoff
- optional debug logging

## 11. 推荐 Python API

底层：

```python
client = WeixinClient.from_default_store()
async for msg in client.poll_messages():
    await client.reply_text(msg, "hello")
```

回调式：

```python
bot = WeixinBot(client, allow_from={"xxx@im.wechat"})

@bot.on_text
async def handle(msg):
    return await ask_claude(msg.text())

await bot.run_forever()
```

## 12. 安全策略

默认建议：

- `allow_from` 支持白名单。
- 不允许缺失 `context_token` 的回复。
- token 文件 `0600`。
- 日志默认脱敏。
- 只做用户消息 `message_type == 1`。
- 使用 `message_id` 去重。
- 对接 Claude/Codex 时默认关闭危险工具。
- 高风险命令必须二次确认。

## 13. 当前实现范围

本 SDK 框架当前实现：

- 异步 HTTP API client。
- QR 登录和 token 存储。
- 消息模型。
- 长轮询。
- 文本回复。
- 文本按通道限制分块。
- cursor 存储。
- 简单 callback bot。
- 多账号 session 文件索引。
- 登录事件流，便于 TUI/Web UI 展示二维码和状态。
- 持久化 message_id 去重。
- AccessPolicy / RateLimit，支持用户、群、触发前缀和限流。
- Markdown 转纯文本。
- 远程媒体 URL 下载后发送。
- 错误通知可选发送。
- typing ticket 获取、typing/cancel typing。
- typing ticket 内存缓存。
- 媒体 AES-128-ECB 加解密。
- CDN 上传/下载 URL 构造。
- 本地图片/视频/文件上传并发送。
- 可选缩略图上传与 `thumb_media` payload。
- 远程媒体 URL 下载后上传发送。
- 入站图片/文件/视频/语音媒体下载和解密。
- 入站语音可选转换 hook。
- BotEvent 事件回调，方便 TUI/Web/trace 集成。
- JsonlEventWriter 和脱敏工具。
- 同步客户端 wrapper。
- session expired 后持久暂停，避免重启后反复请求。
- 配置对象：RetryConfig、SessionGuardConfig、MediaConfig。
- 并发模式：serial、per-conversation、concurrent。
- 去重状态批量 flush，减少热路径写盘。
- HTTP 连接池复用。
- 状态文件原子写，降低崩溃导致 JSON 损坏的概率。
- Bot 并发背压，避免消息洪峰时无限创建任务。
- echo 示例。
- Claude bridge 示例。

未实现：

- OpenClaw adapter。
- 更完整群聊策略，包括精确 mention 解析、群管理员和会话隔离。
- 高级鉴权/pairing。
- 断点续传。
- 内置 SILK 语音转 WAV；目前通过 hook 交给调用方。
- 自动生成图片/视频缩略图；目前支持调用方传入 thumb_path。
