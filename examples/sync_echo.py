#!/usr/bin/env python3
"""Synchronous echo example."""

from __future__ import annotations

from weixin_channel import SyncWeixinClient


def main() -> None:
    with SyncWeixinClient.from_default_store() as wx:
        if wx.session is None:
            raise SystemExit("No saved session. Run the async echo_bot.py --login first.")
        print("Sync echo bot is running. Press Ctrl+C to stop.")
        for msg in wx.iter_messages():
            if msg.is_user_message:
                wx.reply_text(msg, f"sync echo: {msg.text()}")


if __name__ == "__main__":
    main()
