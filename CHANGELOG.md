# Changelog

## 0.2.0 - 2026-04-18

- Added iLink app headers and encoded client version support for all GET/POST requests.
- Added QR login redirect handling for `scaned_but_redirect`.
- Added CDN `upload_full_url` and inbound media `full_url` support.
- Added item-level `IncomingMessage`, `WeixinClient.incoming_messages()`, and item-level bot handlers.
- Added `reply_markdown()` and `IncomingMessage.reply_*` / `download()` / `save()` helpers.
- Improved getupdates API error handling.
- Added configuration validation, byte-aware text chunking, recent-order seen-id retention, and shared HTTP client usage for media paths.

## 0.1.0

- Initial unofficial Python SDK for the Weixin ClawBot/iLink channel protocol.
- Supports QR login, long polling, text replies, typing indicators, media upload/download, access policies, bot callbacks, CLI login, and sync/async clients.
