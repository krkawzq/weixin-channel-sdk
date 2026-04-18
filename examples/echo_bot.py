#!/usr/bin/env python3
"""Echo bot example for weixin_channel.

Run:

    python examples/echo_bot.py --login
    python examples/echo_bot.py
"""

from __future__ import annotations

import argparse
import asyncio

from weixin_channel import StateStore, WeixinBot, WeixinClient


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="force QR-code login")
    parser.add_argument(
        "--allow-from",
        action="append",
        default=None,
        help="Only respond to this from_user_id. Can be repeated.",
    )
    args = parser.parse_args()

    store = StateStore()
    if args.login:
        client = await WeixinClient.login(store=store)
    else:
        client = WeixinClient.from_default_store(store=store)
        if client.session is None:
            client = await WeixinClient.login(store=store)

    allow_from = set(args.allow_from) if args.allow_from else None
    bot = WeixinBot(client, allow_from=allow_from)

    @bot.on_text
    async def echo(msg):
        return f"echo: {msg.text()}"

    @bot.on_error
    async def log_error(exc):
        print(f"[weixin] error: {exc!r}")

    async with client:
        print("Weixin echo bot is running. Press Ctrl+C to stop.")
        await bot.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
