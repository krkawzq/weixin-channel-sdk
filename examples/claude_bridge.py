#!/usr/bin/env python3
"""Bridge Weixin text messages to Claude Agent SDK.

Install this package and Claude Agent SDK first:

    uv pip install -e forks/weixin-channel-sdk
    uv add claude-agent-sdk

Run:

    python examples/claude_bridge.py --login
    python examples/claude_bridge.py --allow-from '<your_user_id@im.wechat>'
"""

from __future__ import annotations

import argparse
import asyncio
import shutil

from weixin_channel import AccessPolicy, RateLimit, StateStore, WeixinBot, WeixinClient


async def ask_claude(prompt: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    claude_cli = shutil.which("claude")
    options = ClaudeAgentOptions(
        cli_path=claude_cli,
        max_turns=1,
        tools=[],
        system_prompt=(
            "You are replying inside Weixin. Keep answers concise and plain text. "
            "Do not use tools in this demo."
        ),
    )

    parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(part for part in parts if part).strip() or "Claude did not return text."


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
    client.plain_text = True

    policy = AccessPolicy(
        allow_users=set(args.allow_from) if args.allow_from else None,
        user_rate_limit=RateLimit(max_events=6, window_seconds=60),
    )
    bot = WeixinBot(client, policy=policy, typing=True, send_error_notice=True)

    @bot.on_text
    async def handle(msg):
        text = msg.text()
        if not text:
            return "I can only handle text in this demo."
        return await ask_claude(text)

    @bot.on_error
    async def log_error(exc):
        print(f"[weixin] error: {exc!r}")

    async with client:
        print("Weixin Claude bridge is running. Press Ctrl+C to stop.")
        await bot.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
