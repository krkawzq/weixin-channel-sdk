# weixin-channel

Unofficial Python SDK for the Weixin ClawBot/iLink channel protocol.

This project is community-maintained and is not affiliated with, endorsed by,
or supported by Tencent or Weixin/WeChat. It is intended for developers who
already have authorized ClawBot/iLink access and want to build custom Python
integrations.

The current implementation covers the known protocol surface needed for custom
developer integrations:

- QR-code login
- QR login progress events for custom UIs
- token/session persistence
- multi-account session index
- `getupdates` long polling
- `sendmessage` text replies
- text chunking
- persistent message de-duplication
- cursor persistence
- typing indicator helpers
- typing ticket cache
- media upload via Weixin CDN
- optional thumbnail upload for images/videos
- inbound media download/decrypt
- optional voice conversion hook for downloaded voice media
- simple callback runner for custom tools or Claude/Codex bridges
- optional access policy for users/groups/rate limits
- bot runtime events for TUI/Web/trace integrations
- JSONL event writer and redaction helper
- Markdown-to-plain-text cleanup for model replies
- synchronous wrapper for simple scripts
- persisted session pause after token expiration
- configurable retry, media limits, auto-thumbnail, and concurrency modes

OpenClaw runtime adapters are intentionally not included. This package is a pure
Weixin channel SDK for direct developer integration.

## Install

```bash
pip install weixin-channel
```

For local development:

```bash
uv pip install -e forks/weixin-channel-sdk
```

## Minimal Echo Bot

```python
import asyncio
from weixin_channel import WeixinBot, WeixinClient


async def main() -> None:
    client = WeixinClient.from_default_store()
    if not client.session:
        client = await WeixinClient.login()

    bot = WeixinBot(client)

    @bot.on_text
    async def echo(msg):
        return f"echo: {msg.text()}"

    await bot.run_forever()


asyncio.run(main())
```

## Access Policy

```python
from weixin_channel import AccessPolicy, RateLimit, WeixinBot

policy = AccessPolicy(
    allow_users={"your_user_id@im.wechat"},
    group_enabled=True,
    allow_groups={"group_id"},
    group_trigger_prefixes=("/", "@bot"),
    user_rate_limit=RateLimit(max_events=6, window_seconds=60),
)

bot = WeixinBot(client, policy=policy, typing=True, send_error_notice=True)

@bot.on_event
async def trace(event):
    print(event.type, event.data)
```

Persist events as JSONL:

```python
from weixin_channel import JsonlEventWriter

writer = JsonlEventWriter("./weixin-events.jsonl")

@bot.on_event
async def trace(event):
    writer.write(event.type, message=event.message, **event.data)
```

## Examples

```bash
python examples/echo_bot.py --login
python examples/echo_bot.py
python examples/claude_bridge.py
python examples/media_bot.py
python examples/sync_echo.py
```

## CLI Login

After installation, use the CLI to save a Weixin session:

```bash
weixin-channel login
```

Useful options:

```bash
weixin-channel login --state-dir ~/.weixin-channel
weixin-channel login --no-qr
weixin-channel login --timeout 480 --max-refreshes 3
```

The command stores the session token in the SDK state directory and prints the
account id, user id, and base URL after successful authorization.

## Safety

This package assumes you are using a Weixin account and ClawBot/iLink access that
you are authorized to use. Do not use it for unsolicited messaging, scraping,
account abuse, bypassing client restrictions, or unsafe tool execution.

For any tool/agent bridge, configure an allowlist before enabling tools that can
read files, run commands, mutate systems, or access private data.

## Media

Send a local file:

```python
await client.reply_media_file(msg, "/absolute/path/to/report.pdf", text="Report attached.")
await client.reply_media_file(msg, "/absolute/path/to/image.png", thumb_path="/absolute/path/to/thumb.jpg")
await client.reply_remote_media(msg, "https://example.com/image.png", text="Downloaded and sent.")
```

Download media from an inbound message:

```python
downloads = await client.download_message_media(msg, dest_dir="./downloads")
for item in downloads:
    print(item.path, item.mime_type)
```

Media support follows the original Weixin channel behavior:

- files are AES-128-ECB encrypted before CDN upload
- `getuploadurl` is used to get the upload parameter
- CDN response header `x-encrypted-param` is used as the downstream media reference
- inbound media is downloaded from CDN and decrypted when an AES key is present

Voice conversion can be supplied as a hook:

```python
async def silk_to_wav(raw: bytes) -> bytes:
    ...

downloads = await client.download_message_media(msg, dest_dir="./downloads", voice_converter=silk_to_wav)
```

## Login Events

For a custom TUI/Web UI:

```python
async for event in WeixinClient.login_events():
    if event.type == "qrcode":
        print(event.qrcode_url)
    elif event.type == "connected":
        print(event.session.account_id)
```

## Sync Wrapper

```python
from weixin_channel import SyncWeixinClient

with SyncWeixinClient.from_default_store() as wx:
    for msg in wx.iter_messages():
        wx.reply_text(msg, f"echo: {msg.text()}")
```

## Configuration Objects

```python
from weixin_channel import ConcurrencyConfig, MediaConfig, RetryConfig, SessionGuardConfig, WeixinClient

client = WeixinClient.from_default_store()
client.retry = RetryConfig(max_consecutive_failures=3)
client.session_guard = SessionGuardConfig(pause_on_expired=True, pause_seconds=3600)
client.media = MediaConfig(auto_thumbnail=True, thumbnail_size=(320, 320))
client.concurrency = ConcurrencyConfig(mode="per-conversation", max_concurrency=4)
```

Recommended runtime modes:

- `serial`: safest; one message at a time.
- `per-conversation`: parallel across users/groups, ordered within each conversation.
- `concurrent`: fastest; no per-conversation ordering guarantee.

For long-running bridges, `seen_flush_interval` controls how often processed
message ids are flushed to disk. Larger values reduce I/O; smaller values reduce
duplicate risk after crashes.
