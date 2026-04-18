"""Command line interface for weixin-channel."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .api import DEFAULT_BASE_URL, DEFAULT_BOT_TYPE
from .client import terminal_qr_renderer
from .errors import WeixinChannelError
from .store import StateStore
from .client import WeixinClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weixin-channel",
        description="Developer CLI for the Weixin ClawBot/iLink channel SDK.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Interactive QR-code login")
    login.add_argument("--state-dir", help="State directory for token/cursor files")
    login.add_argument("--base-url", default=DEFAULT_BASE_URL, help="iLink API base URL")
    login.add_argument("--bot-type", default=DEFAULT_BOT_TYPE, help="iLink bot_type")
    login.add_argument("--timeout", type=float, default=480.0, help="Login timeout in seconds")
    login.add_argument("--max-refreshes", type=int, default=3, help="Maximum QR refresh attempts")
    login.add_argument(
        "--no-env-proxy",
        action="store_true",
        help="Ignore HTTP(S)_PROXY/ALL_PROXY environment variables",
    )
    login.add_argument(
        "--no-qr",
        action="store_true",
        help="Do not render an ASCII QR code; only print the QR URL",
    )
    return parser


async def run_login(args: argparse.Namespace) -> int:
    store = StateStore(args.state_dir) if args.state_dir else StateStore()

    print("Starting Weixin QR login...")
    print(f"State dir: {store.root}")
    print()

    async for event in WeixinClient.login_events(
        store=store,
        base_url=args.base_url,
        bot_type=args.bot_type,
        timeout_s=args.timeout,
        max_qr_refreshes=args.max_refreshes,
        trust_env=not args.no_env_proxy,
    ):
        if event.type == "qrcode":
            assert event.qrcode_url is not None
            print("Scan this QR code with Weixin and confirm authorization:")
            if args.no_qr:
                print(event.qrcode_url)
            else:
                terminal_qr_renderer(event.qrcode_url)
            print()
            continue

        if event.type == "scaned":
            print("Scanned. Please confirm in Weixin...")
            continue

        if event.type == "redirected":
            print("Login polling redirected. Continuing...")
            continue

        if event.type == "wait":
            print(".", end="", flush=True)
            continue

        if event.type == "expired":
            print("\nQR code expired. Refreshing...")
            continue

        if event.type == "connected":
            session = event.session
            print("\nLogin succeeded.")
            if session is not None:
                print(f"Account ID: {session.account_id or '(unknown)'}")
                print(f"User ID: {session.user_id or '(unknown)'}")
                print(f"Base URL: {session.base_url}")
            print(f"Session file: {store.session_path}")
            return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            return asyncio.run(run_login(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except WeixinChannelError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
