"""Synchronous convenience wrapper for simple scripts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

from .client import QrRenderer, WeixinClient
from .models import AccountSession, DownloadedMedia, GetUpdatesResponse, WeixinMessage
from .store import StateStore


class SyncWeixinClient:
    """Sync wrapper around WeixinClient using one private event loop.

    This is intended for simple scripts. Async applications should use
    `WeixinClient` directly.
    """

    def __init__(self, client: WeixinClient) -> None:
        self.client = client
        self._loop = asyncio.new_event_loop()

    @classmethod
    def from_default_store(
        cls,
        *,
        store: StateStore | None = None,
        account_id: str | None = None,
    ) -> "SyncWeixinClient":
        return cls(WeixinClient.from_default_store(store=store, account_id=account_id))

    @classmethod
    def login(
        cls,
        *,
        store: StateStore | None = None,
        renderer: QrRenderer | None = None,
    ) -> "SyncWeixinClient":
        client = cls(WeixinClient(session=None, store=store))
        client.client = client._run(WeixinClient.login(store=store, renderer=renderer))
        return client

    @property
    def session(self) -> AccountSession | None:
        return self.client.session

    def _run(self, coro):  # type: ignore[no-untyped-def]
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        try:
            self._run(self.client.close())
        finally:
            if not self._loop.is_closed():
                self._loop.close()

    def __enter__(self) -> "SyncWeixinClient":
        self._run(self.client.__aenter__())
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def get_updates(self, cursor: str | None = None) -> GetUpdatesResponse:
        return self._run(self.client.get_updates(cursor))

    def iter_messages(self) -> Iterator[WeixinMessage]:
        agen = self.client.poll_messages()
        try:
            while True:
                yield self._run(agen.__anext__())
        except StopAsyncIteration:
            return

    def reply_text(self, message: WeixinMessage, text: str) -> str:
        return self._run(self.client.reply_text(message, text))

    def reply_media_file(self, message: WeixinMessage, file_path: str | Path, *, text: str = "") -> str:
        return self._run(self.client.reply_media_file(message, file_path, text=text))

    def reply_remote_media(self, message: WeixinMessage, url: str, *, text: str = "") -> str:
        return self._run(self.client.reply_remote_media(message, url, text=text))

    def download_message_media(self, message: WeixinMessage, *, dest_dir: str | Path) -> list[DownloadedMedia]:
        return self._run(self.client.download_message_media(message, dest_dir=dest_dir))
