"""High-level Weixin channel client."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from .api import DEFAULT_BASE_URL, DEFAULT_BOT_TYPE, DEFAULT_CDN_BASE_URL, WeixinApi
from .cdn import (
    VoiceConverter,
    build_media_message_item,
    download_media_item,
    download_remote_file,
    media_type_for_file,
    upload_media_file,
)
from .config import ConcurrencyConfig, MediaConfig, RetryConfig, SessionGuardConfig
from .errors import WeixinLoginError, WeixinProtocolError, WeixinSessionExpired
from .models import (
    AccountSession,
    DownloadedMedia,
    GetUpdatesResponse,
    QrStatusResponse,
    TypingStatus,
    UploadedMedia,
    WeixinMessage,
)
from .store import StateStore
from .text import markdown_to_plain_text
from .thumbnail import make_thumbnail
from .utils import generate_client_id

if TYPE_CHECKING:
    from .incoming import IncomingMessage


QrRenderer = Callable[[str], None]


@dataclass(slots=True)
class LoginEvent:
    type: str
    message: str
    qrcode_url: str | None = None
    status: QrStatusResponse | None = None
    session: AccountSession | None = None


@dataclass(slots=True)
class _TypingCacheEntry:
    ticket: str
    expires_at: float
    retry_after: float = 0.0
    retry_delay: float = 2.0


def print_qr_url(url: str) -> None:
    print(f"QR Code URL: {url}")


def terminal_qr_renderer(url: str) -> None:
    """Best-effort terminal QR rendering.

    Falls back to printing the URL if the QR renderer dependency is unavailable.
    """
    try:
        import qrcode
    except ModuleNotFoundError:
        print_qr_url(url)
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print_qr_url(url)


class WeixinClient:
    """High-level client for text-based Weixin ClawBot integrations."""

    def __init__(
        self,
        *,
        session: AccountSession | None = None,
        store: StateStore | None = None,
        api: WeixinApi | None = None,
        route_tag: str | None = None,
        trust_env: bool = True,
        cdn_base_url: str = DEFAULT_CDN_BASE_URL,
        plain_text: bool = False,
        retry: RetryConfig | None = None,
        session_guard: SessionGuardConfig | None = None,
        media: MediaConfig | None = None,
        concurrency: ConcurrencyConfig | None = None,
    ) -> None:
        self.store = store or StateStore()
        self.session = session
        self.cdn_base_url = cdn_base_url
        self.plain_text = plain_text
        self.retry = retry or RetryConfig()
        self.session_guard = session_guard or SessionGuardConfig()
        self.media = media or MediaConfig()
        self.concurrency = concurrency or ConcurrencyConfig()
        self._session_paused_until = 0.0
        self.api = api or WeixinApi(
            base_url=session.base_url if session else DEFAULT_BASE_URL,
            token=session.token if session else None,
            route_tag=route_tag,
            trust_env=trust_env,
        )
        self._seen_message_ids: OrderedDict[int, None] = OrderedDict()
        self._typing_cache: dict[str, _TypingCacheEntry] = {}
        self._seen_dirty_count = 0
        if session and session.account_id:
            self._seen_message_ids.update(
                (message_id, None) for message_id in self.store.load_seen_message_ids(session.account_id)
            )
            remaining = self.store.load_pause_remaining(session.account_id)
            if remaining > 0:
                self._session_paused_until = time.monotonic() + remaining

    @classmethod
    def from_default_store(
        cls,
        *,
        store: StateStore | None = None,
        account_id: str | None = None,
    ) -> "WeixinClient":
        resolved_store = store or StateStore()
        return cls(session=resolved_store.load_session(account_id), store=resolved_store)

    @classmethod
    async def login(
        cls,
        *,
        store: StateStore | None = None,
        base_url: str = DEFAULT_BASE_URL,
        bot_type: str = DEFAULT_BOT_TYPE,
        timeout_s: float = 480.0,
        max_qr_refreshes: int = 3,
        renderer: QrRenderer | None = terminal_qr_renderer,
        trust_env: bool = True,
    ) -> "WeixinClient":
        resolved_store = store or StateStore()
        api = WeixinApi(base_url=base_url, trust_env=trust_env)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            refresh_count = 0
            session: AccountSession | None = None

            while loop.time() < deadline:
                qr = await api.get_bot_qrcode(bot_type=bot_type)
                if renderer is not None:
                    renderer(qr.qrcode_img_content)

                status = await cls._wait_for_login(
                    api,
                    qr.qrcode,
                    base_url=base_url,
                    deadline=deadline,
                )
                if status.status == "confirmed":
                    if not status.bot_token:
                        raise WeixinLoginError("login confirmed but bot_token was missing")
                    session = AccountSession.create(
                        token=status.bot_token,
                        base_url=status.baseurl or base_url,
                        account_id=status.ilink_bot_id,
                        user_id=status.ilink_user_id,
                    )
                    break

                if status.status == "expired":
                    refresh_count += 1
                    if refresh_count > max_qr_refreshes:
                        raise WeixinLoginError("QR code expired too many times")
                    continue

            if session is None:
                raise WeixinLoginError("QR login timed out")

            resolved_store.save_session(session)
            await api.close()
            return cls(session=session, store=resolved_store)
        except Exception:
            await api.close()
            raise

    @classmethod
    async def login_events(
        cls,
        *,
        store: StateStore | None = None,
        base_url: str = DEFAULT_BASE_URL,
        bot_type: str = DEFAULT_BOT_TYPE,
        timeout_s: float = 480.0,
        max_qr_refreshes: int = 3,
        trust_env: bool = True,
    ) -> AsyncIterator[LoginEvent]:
        """Yield QR login progress events for UIs and CLIs."""
        resolved_store = store or StateStore()
        api = WeixinApi(base_url=base_url, trust_env=trust_env)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            refresh_count = 0
            while loop.time() < deadline:
                qr = await api.get_bot_qrcode(bot_type=bot_type)
                yield LoginEvent("qrcode", "Scan this QR code in Weixin.", qrcode_url=qr.qrcode_img_content)
                status = QrStatusResponse(status="wait")
                polling_base_url = base_url
                while loop.time() < deadline:
                    status = await api.get_qrcode_status(qr.qrcode, base_url=polling_base_url)
                    if status.status == "scaned_but_redirect":
                        if status.redirect_host:
                            polling_base_url = _redirect_base_url(status.redirect_host)
                        yield LoginEvent("redirected", "QR login redirected.", status=status)
                    else:
                        yield LoginEvent(status.status, f"QR login status: {status.status}", status=status)
                    if status.status in {"confirmed", "expired"}:
                        break
                    await asyncio.sleep(1.0)
                if status.status == "confirmed":
                    if not status.bot_token:
                        raise WeixinLoginError("login confirmed but bot_token was missing")
                    session = AccountSession.create(
                        token=status.bot_token,
                        base_url=status.baseurl or base_url,
                        account_id=status.ilink_bot_id,
                        user_id=status.ilink_user_id,
                    )
                    resolved_store.save_session(session)
                    yield LoginEvent("connected", "Weixin connected.", status=status, session=session)
                    return
                if status.status == "expired":
                    refresh_count += 1
                    if refresh_count > max_qr_refreshes:
                        raise WeixinLoginError("QR code expired too many times")
            raise WeixinLoginError("QR login timed out")
        finally:
            await api.close()

    @staticmethod
    async def _wait_for_login(
        api: WeixinApi,
        qrcode: str,
        *,
        base_url: str,
        deadline: float,
    ) -> QrStatusResponse:
        polling_base_url = base_url
        while asyncio.get_running_loop().time() < deadline:
            status = await api.get_qrcode_status(qrcode, base_url=polling_base_url)
            if status.status == "scaned_but_redirect":
                if status.redirect_host:
                    polling_base_url = _redirect_base_url(status.redirect_host)
                await asyncio.sleep(1.0)
                continue
            if status.status in {"confirmed", "expired"}:
                return status
            await asyncio.sleep(1.0)
        return QrStatusResponse(status="expired")

    async def __aenter__(self) -> "WeixinClient":
        await self.api.__aenter__()
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    async def close(self) -> None:
        self.flush_seen_message_ids()
        await self.api.close()

    def require_session(self) -> AccountSession:
        if self.session is None:
            raise WeixinLoginError("not logged in; call WeixinClient.login() first")
        return self.session

    async def get_updates(self, cursor: str | None = None) -> GetUpdatesResponse:
        session = self.require_session()
        self._raise_if_session_paused()
        resolved_cursor = cursor
        if resolved_cursor is None:
            resolved_cursor = self.store.load_cursor(session.account_id)
        resp = await self.api.get_updates(resolved_cursor)
        if resp.get_updates_buf:
            self.store.save_cursor(resp.get_updates_buf, session.account_id)
        return resp

    async def poll_messages(
        self,
        *,
        dedupe: bool = True,
        sleep_on_empty_s: float = 0.2,
        retry_delay_s: float | None = None,
        backoff_delay_s: float | None = None,
        max_consecutive_failures: int | None = None,
    ) -> AsyncIterator[WeixinMessage]:
        """Yield inbound messages forever."""
        session = self.require_session()
        cursor = self.store.load_cursor(session.account_id)
        consecutive_failures = 0
        while True:
            try:
                self._raise_if_session_paused()
                resp = await self.api.get_updates(cursor)
            except WeixinSessionExpired:
                self._pause_session_if_configured()
                raise
            except Exception:
                consecutive_failures += 1
                max_failures = max_consecutive_failures or self.retry.max_consecutive_failures
                if consecutive_failures >= max_failures:
                    consecutive_failures = 0
                    await asyncio.sleep(backoff_delay_s if backoff_delay_s is not None else self.retry.backoff_delay_s)
                else:
                    await asyncio.sleep(retry_delay_s if retry_delay_s is not None else self.retry.retry_delay_s)
                continue
            consecutive_failures = 0
            if resp.get_updates_buf:
                cursor = resp.get_updates_buf
                self.store.save_cursor(cursor, session.account_id)

            yielded = False
            for msg in resp.msgs:
                if dedupe and msg.message_id is not None:
                    if msg.message_id in self._seen_message_ids:
                        self._seen_message_ids.move_to_end(msg.message_id)
                        continue
                    self._seen_message_ids[msg.message_id] = None
                    if len(self._seen_message_ids) > self.retry.seen_cache_limit:
                        self._seen_message_ids.popitem(last=False)
                    if session.account_id:
                        self._seen_dirty_count += 1
                        if self._seen_dirty_count >= self.retry.seen_flush_interval:
                            self.flush_seen_message_ids()
                yielded = True
                yield msg

            if not yielded:
                await asyncio.sleep(sleep_on_empty_s)

    async def incoming_messages(
        self,
        *,
        dedupe: bool = True,
        sleep_on_empty_s: float = 0.2,
        retry_delay_s: float | None = None,
        backoff_delay_s: float | None = None,
        max_consecutive_failures: int | None = None,
    ) -> AsyncIterator["IncomingMessage"]:
        """Yield item-level inbound messages for developer-friendly handlers."""
        from .incoming import IncomingMessage

        async for msg in self.poll_messages(
            dedupe=dedupe,
            sleep_on_empty_s=sleep_on_empty_s,
            retry_delay_s=retry_delay_s,
            backoff_delay_s=backoff_delay_s,
            max_consecutive_failures=max_consecutive_failures,
        ):
            for item in msg.item_list:
                yield IncomingMessage(client=self, raw_message=msg, item=item)

    async def send_text(
        self,
        *,
        to_user_id: str,
        text: str,
        context_token: str,
        client_id: str | None = None,
        chunk_limit: int = 4000,
    ) -> str:
        if not to_user_id:
            raise WeixinProtocolError("to_user_id is required")
        if not context_token:
            raise WeixinProtocolError("context_token is required")
        self._raise_if_session_paused()
        if self.plain_text:
            text = markdown_to_plain_text(text)
        chunks = _chunk_text(text, chunk_limit)
        last_client_id = ""
        for index, chunk in enumerate(chunks):
            resolved_client_id = client_id if index == 0 and client_id else generate_client_id()
            await self.api.send_text(
                to_user_id=to_user_id,
                text=chunk,
                context_token=context_token,
                client_id=resolved_client_id,
            )
            last_client_id = resolved_client_id
        return last_client_id

    async def send_markdown(
        self,
        *,
        to_user_id: str,
        markdown: str,
        context_token: str,
        client_id: str | None = None,
        chunk_limit: int = 4000,
    ) -> str:
        return await self.send_text(
            to_user_id=to_user_id,
            text=markdown_to_plain_text(markdown),
            context_token=context_token,
            client_id=client_id,
            chunk_limit=chunk_limit,
        )

    async def send_media_file(
        self,
        *,
        to_user_id: str,
        file_path: str | Path,
        context_token: str,
        text: str = "",
        client_id: str | None = None,
        cdn_base_url: str | None = None,
        thumb_path: str | Path | None = None,
    ) -> str:
        if not to_user_id:
            raise WeixinProtocolError("to_user_id is required")
        if not context_token:
            raise WeixinProtocolError("context_token is required")
        self._raise_if_session_paused()
        path_obj = Path(file_path)
        if self.media.max_upload_bytes > 0 and path_obj.stat().st_size > self.media.max_upload_bytes:
            raise WeixinProtocolError(f"media upload exceeds max_upload_bytes={self.media.max_upload_bytes}")
        resolved_thumb = Path(thumb_path) if thumb_path is not None else None
        if resolved_thumb is None and self.media.auto_thumbnail:
            resolved_thumb = make_thumbnail(
                path_obj,
                dest_dir=self.store.root / "thumbs",
                size=self.media.thumbnail_size,
            )

        media_type = media_type_for_file(path_obj)
        uploaded = await upload_media_file(
            api=self.api,
            file_path=path_obj,
            to_user_id=to_user_id,
            media_type=media_type,
            cdn_base_url=cdn_base_url or self.cdn_base_url,
            http_client=self.api._ensure_client(),
            thumb_path=resolved_thumb,
        )
        return await self.send_uploaded_media(
            to_user_id=to_user_id,
            uploaded=uploaded,
            context_token=context_token,
            text=text,
            client_id=client_id,
        )

    async def send_uploaded_media(
        self,
        *,
        to_user_id: str,
        uploaded: UploadedMedia,
        context_token: str,
        text: str = "",
        client_id: str | None = None,
    ) -> str:
        if not to_user_id:
            raise WeixinProtocolError("to_user_id is required")
        if not context_token:
            raise WeixinProtocolError("context_token is required")
        self._raise_if_session_paused()

        resolved_client_id = client_id or generate_client_id()
        item_list = []
        if text:
            if self.plain_text:
                text = markdown_to_plain_text(text)
            item_list.append({"type": 1, "text_item": {"text": text}})
        item_list.append(build_media_message_item(uploaded))
        await self.api.send_message(
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": resolved_client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": item_list,
                }
            }
        )
        return resolved_client_id

    async def reply_text(
        self,
        message: WeixinMessage,
        text: str,
        *,
        client_id: str | None = None,
    ) -> str:
        return await self.send_text(
            to_user_id=message.sender_id,
            text=text,
            context_token=message.context_token or "",
            client_id=client_id,
        )

    async def reply_markdown(
        self,
        message: WeixinMessage,
        markdown: str,
        *,
        client_id: str | None = None,
    ) -> str:
        return await self.send_markdown(
            to_user_id=message.sender_id,
            markdown=markdown,
            context_token=message.context_token or "",
            client_id=client_id,
        )

    async def reply_media_file(
        self,
        message: WeixinMessage,
        file_path: str | Path,
        *,
        text: str = "",
        client_id: str | None = None,
        cdn_base_url: str | None = None,
        thumb_path: str | Path | None = None,
    ) -> str:
        return await self.send_media_file(
            to_user_id=message.sender_id,
            file_path=file_path,
            context_token=message.context_token or "",
            text=text,
            client_id=client_id,
            cdn_base_url=cdn_base_url,
            thumb_path=thumb_path,
        )

    async def send_remote_media(
        self,
        *,
        to_user_id: str,
        url: str,
        context_token: str,
        text: str = "",
        client_id: str | None = None,
        download_dir: str | Path | None = None,
    ) -> str:
        dest = Path(download_dir) if download_dir is not None else self.store.root / "remote-media"
        local_path = await download_remote_file(
            url,
            dest_dir=dest,
            http_client=self.api._ensure_client(),
            max_bytes=self.media.max_download_bytes,
        )
        return await self.send_media_file(
            to_user_id=to_user_id,
            file_path=local_path,
            context_token=context_token,
            text=text,
            client_id=client_id,
            cdn_base_url=self.cdn_base_url,
        )

    async def reply_remote_media(
        self,
        message: WeixinMessage,
        url: str,
        *,
        text: str = "",
        client_id: str | None = None,
        download_dir: str | Path | None = None,
    ) -> str:
        return await self.send_remote_media(
            to_user_id=message.sender_id,
            url=url,
            context_token=message.context_token or "",
            text=text,
            client_id=client_id,
            download_dir=download_dir,
        )

    async def download_message_media(
        self,
        message: WeixinMessage,
        *,
        dest_dir: str | Path,
        cdn_base_url: str | None = None,
        voice_converter: VoiceConverter | None = None,
    ) -> list[DownloadedMedia]:
        downloads: list[DownloadedMedia] = []
        for item in message.media_items():
            downloaded = await download_media_item(
                item,
                dest_dir=dest_dir,
                cdn_base_url=cdn_base_url or self.cdn_base_url,
                http_client=self.api._ensure_client(),
                voice_converter=voice_converter,
                max_bytes=self.media.max_download_bytes,
            )
            if downloaded is not None:
                downloads.append(downloaded)
        return downloads

    async def get_typing_ticket(self, message_or_user: WeixinMessage | str) -> str:
        if isinstance(message_or_user, WeixinMessage):
            user_id = message_or_user.sender_id
            context_token = message_or_user.context_token
        else:
            user_id = message_or_user
            context_token = None
        now = time.monotonic()
        cached = self._typing_cache.get(user_id)
        if cached and cached.expires_at > now:
            return cached.ticket
        if cached and cached.retry_after > now:
            return ""
        try:
            resp = await self.api.get_config(ilink_user_id=user_id, context_token=context_token)
        except Exception:
            retry_delay = min((cached.retry_delay * 2 if cached else 2.0), 3600.0)
            self._typing_cache[user_id] = _TypingCacheEntry(
                ticket=cached.ticket if cached else "",
                expires_at=0.0,
                retry_after=now + retry_delay,
                retry_delay=retry_delay,
            )
            return cached.ticket if cached and cached.expires_at > now else ""
        ticket = resp.typing_ticket or ""
        if ticket:
            self._typing_cache[user_id] = _TypingCacheEntry(
                ticket=ticket,
                expires_at=now + 24 * 60 * 60,
                retry_after=0.0,
                retry_delay=2.0,
            )
        return ticket

    async def send_typing(
        self,
        message_or_user: WeixinMessage | str,
        *,
        typing_ticket: str | None = None,
        status: TypingStatus = TypingStatus.TYPING,
    ) -> None:
        if isinstance(message_or_user, WeixinMessage):
            user_id = message_or_user.sender_id
        else:
            user_id = message_or_user
        ticket = typing_ticket or await self.get_typing_ticket(message_or_user)
        if not ticket:
            return
        await self.api.send_typing(
            ilink_user_id=user_id,
            typing_ticket=ticket,
            status=status,
        )

    def _pause_session_if_configured(self) -> None:
        if self.session_guard.pause_on_expired:
            self._session_paused_until = time.monotonic() + self.session_guard.pause_seconds
            if self.session and self.session.account_id:
                self.store.save_pause_until(self._session_paused_until, self.session.account_id)

    def _raise_if_session_paused(self) -> None:
        if self._session_paused_until and time.monotonic() < self._session_paused_until:
            remaining = int(self._session_paused_until - time.monotonic())
            raise WeixinSessionExpired(f"Weixin session is paused after expiration; retry in {remaining}s")

    def flush_seen_message_ids(self) -> None:
        if not self.session or not self.session.account_id or self._seen_dirty_count <= 0:
            return
        retained = list(self._seen_message_ids.keys())[-self.retry.seen_cache_limit :]
        self.store.save_seen_message_ids(retained, self.session.account_id)
        self._seen_message_ids = OrderedDict((message_id, None) for message_id in retained)
        self._seen_dirty_count = 0

    @asynccontextmanager
    async def typing(self, message_or_user: WeixinMessage | str) -> AsyncIterator[None]:
        ticket = await self.get_typing_ticket(message_or_user)
        if ticket:
            await self.send_typing(message_or_user, typing_ticket=ticket, status=TypingStatus.TYPING)
        try:
            yield
        finally:
            if ticket:
                await self.send_typing(message_or_user, typing_ticket=ticket, status=TypingStatus.CANCEL)


def quote_qrcode(qrcode: str) -> str:
    """Expose URL encoding for callers that need raw login polling."""
    return quote(qrcode, safe="")


def _redirect_base_url(redirect_host: str) -> str:
    host = redirect_host.strip().rstrip("/")
    if host.startswith(("http://", "https://")):
        return host
    return f"https://{host}"


def _chunk_text(text: str, limit: int) -> list[str]:
    if limit <= 0 or len(text.encode("utf-8")) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for char in text:
        char_size = len(char.encode("utf-8"))
        if current and current_size + char_size > limit:
            chunks.append("".join(current))
            current = []
            current_size = 0
        current.append(char)
        current_size += char_size
    if current:
        chunks.append("".join(current))
    return chunks or [text]
