#!/usr/bin/env python3
"""Media demo for weixin_channel.

Behavior:
- If the user sends media, download/decrypt it to the SDK state directory.
- If the user sends `/send <path>`, send that local file back.
- Otherwise reply with a short help message.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from weixin_channel import StateStore, WeixinBot, WeixinClient


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--allow-from", action="append", default=None)
    args = parser.parse_args()

    store = StateStore()
    if args.login:
        client = await WeixinClient.login(store=store)
    else:
        client = WeixinClient.from_default_store(store=store)
        if client.session is None:
            client = await WeixinClient.login(store=store)

    bot = WeixinBot(client, allow_from=set(args.allow_from) if args.allow_from else None, typing=True)
    download_dir = store.root / "downloads"

    @bot.on_message
    async def handle(msg):
        text = msg.text().strip()
        if text.startswith("/send "):
            file_path = Path(text[len("/send ") :].strip()).expanduser()
            await client.reply_media_file(msg, file_path, text=f"Sending {file_path.name}")
            return None

        if msg.media_items():
            downloads = await client.download_message_media(msg, dest_dir=download_dir)
            if not downloads:
                return "I saw media, but could not download it."
            lines = ["Downloaded media:"]
            for media in downloads:
                lines.append(f"- {media.path} ({media.mime_type or 'unknown'})")
            return "\n".join(lines)

        return "Send media to download it, or send `/send /absolute/path/to/file` to send a file."

    @bot.on_error
    async def log_error(exc):
        print(f"[weixin] error: {exc!r}")

    async with client:
        print("Weixin media bot is running. Press Ctrl+C to stop.")
        await bot.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
