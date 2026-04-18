"""Callback runner for WeixinClient."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from .client import WeixinClient
from .config import ConcurrencyConfig
from .events import BotEvent
from .incoming import IncomingMessage
from .models import MessageItemType, WeixinMessage
from .policy import AccessPolicy

BotMessage = WeixinMessage | IncomingMessage
TextHandler = Callable[[BotMessage], Awaitable[str | None] | str | None]
MessageHandler = Callable[[BotMessage], Awaitable[str | None] | str | None]
RawMessageHandler = Callable[[WeixinMessage], Awaitable[str | None] | str | None]
ErrorHandler = Callable[[BaseException], Awaitable[None] | None]
EventHandler = Callable[[BotEvent], Awaitable[None] | None]


class WeixinBot:
    """Small callback-based bot runner.

    The handler may either:
    - return a string, which will be sent as a reply;
    - return None, meaning it handled the message itself.
    """

    def __init__(
        self,
        client: WeixinClient,
        *,
        allow_from: set[str] | None = None,
        policy: AccessPolicy | None = None,
        typing: bool = False,
        send_error_notice: bool = False,
        concurrency: ConcurrencyConfig | None = None,
        item_level: bool = False,
    ) -> None:
        self.client = client
        self.allow_from = allow_from
        self.policy = policy
        self.typing = typing
        self.send_error_notice = send_error_notice
        self.concurrency = concurrency or client.concurrency
        self.item_level = item_level
        self._semaphore = asyncio.Semaphore(max(1, self.concurrency.max_concurrency))
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._max_pending_tasks = max(1, self.concurrency.max_concurrency * 4)
        self._message_handler: MessageHandler | None = None
        self._raw_message_handler: RawMessageHandler | None = None
        self._text_handler: TextHandler | None = None
        self._media_handler: MessageHandler | None = None
        self._image_handler: MessageHandler | None = None
        self._voice_handler: MessageHandler | None = None
        self._file_handler: MessageHandler | None = None
        self._video_handler: MessageHandler | None = None
        self._error_handler: ErrorHandler | None = None
        self._event_handler: EventHandler | None = None

    def on_message(self, handler: MessageHandler) -> MessageHandler:
        self._message_handler = handler
        return handler

    def on_raw_message(self, handler: RawMessageHandler) -> RawMessageHandler:
        self._raw_message_handler = handler
        return handler

    def on_text(self, handler: TextHandler) -> TextHandler:
        self._text_handler = handler
        return handler

    def on_media(self, handler: MessageHandler) -> MessageHandler:
        self._media_handler = handler
        return handler

    def on_image(self, handler: MessageHandler) -> MessageHandler:
        self._image_handler = handler
        return handler

    def on_voice(self, handler: MessageHandler) -> MessageHandler:
        self._voice_handler = handler
        return handler

    def on_file(self, handler: MessageHandler) -> MessageHandler:
        self._file_handler = handler
        return handler

    def on_video(self, handler: MessageHandler) -> MessageHandler:
        self._video_handler = handler
        return handler

    def on_error(self, handler: ErrorHandler) -> ErrorHandler:
        self._error_handler = handler
        return handler

    def on_event(self, handler: EventHandler) -> EventHandler:
        self._event_handler = handler
        return handler

    async def _maybe_await(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value

    async def _handle_error(self, exc: BaseException) -> None:
        if self._error_handler is None:
            raise exc
        await self._maybe_await(self._error_handler(exc))

    async def _emit(self, event_type: str, message: str = "", **data: Any) -> None:
        if self._event_handler is None:
            return
        try:
            await self._maybe_await(self._event_handler(BotEvent(event_type, message, data=data)))
        except Exception:
            # Observability must not break message handling.
            return

    def _allowed(self, msg: WeixinMessage) -> bool:
        if self.policy is not None:
            return self.policy.allow(msg)
        return self.allow_from is None or msg.sender_id in self.allow_from

    async def run_forever(self) -> None:
        if not self._has_handlers():
            raise RuntimeError("no handler registered; use @bot.on_message, @bot.on_text, or @bot.on_media")

        if self.item_level:
            async for raw_msg in self.client.poll_messages():
                if self._raw_message_handler is not None:
                    await self._dispatch(raw_msg)
                for item in raw_msg.item_list:
                    await self._dispatch(IncomingMessage(client=self.client, raw_message=raw_msg, item=item))
            return

        async for msg in self.client.poll_messages():
            await self._dispatch(msg)

    def _has_handlers(self) -> bool:
        return any(
            handler is not None
            for handler in (
                self._message_handler,
                self._raw_message_handler,
                self._text_handler,
                self._media_handler,
                self._image_handler,
                self._voice_handler,
                self._file_handler,
                self._video_handler,
            )
        )

    async def _dispatch(self, msg: BotMessage) -> None:
        if self.concurrency.mode == "serial":
            await self._process_message(msg)
            return
        task = asyncio.create_task(self._process_message_with_limits(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        done = {task for task in self._tasks if task.done()}
        self._tasks.difference_update(done)
        if len(self._tasks) >= self._max_pending_tasks:
            await self._wait_for_one_task()

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _wait_for_one_task(self) -> None:
        if not self._tasks:
            return
        done, pending = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        self._tasks = set(pending)
        for task in done:
            with contextlib.suppress(Exception):
                task.result()

    async def _process_message_with_limits(self, msg: BotMessage) -> None:
        async with self._semaphore:
            if self.concurrency.mode == "per-conversation":
                lock = self._conversation_locks.setdefault(msg.conversation_id, asyncio.Lock())
                async with lock:
                    await self._process_message(msg)
            else:
                await self._process_message(msg)

    async def _process_message(self, msg: BotMessage) -> None:
        raw_msg = msg.raw_message if isinstance(msg, IncomingMessage) else msg
        await self._emit("message.received", from_user_id=raw_msg.from_user_id, group_id=raw_msg.group_id)
        if not raw_msg.is_user_message:
            await self._emit("message.skipped", "not a user message", reason="not_user")
            return
        if not self._allowed(raw_msg):
            await self._emit("message.skipped", "blocked by policy", reason="policy")
            return
        try:
            handler = self._select_handler(msg)
            if handler is None:
                return

            text = msg.text if isinstance(msg, IncomingMessage) else msg.text()
            await self._emit("handler.start", from_user_id=raw_msg.from_user_id, text=text)
            if self.typing:
                async with self.client.typing(raw_msg):
                    result = await self._maybe_await(handler(msg))
            else:
                result = await self._maybe_await(handler(msg))
            await self._emit("handler.done", from_user_id=raw_msg.from_user_id)

            if isinstance(result, str) and result:
                if isinstance(msg, IncomingMessage):
                    await msg.reply_text(result)
                else:
                    await self.client.reply_text(raw_msg, result)
                await self._emit("reply.sent", from_user_id=raw_msg.from_user_id)
        except BaseException as exc:
            await self._emit("handler.error", str(exc), error=repr(exc))
            if self.send_error_notice and raw_msg.context_token:
                try:
                    await self.client.reply_text(raw_msg, f"⚠️ 处理失败：{str(exc)[:200]}")
                except Exception:
                    pass
            await self._handle_error(exc)

    def _select_handler(self, msg: BotMessage) -> MessageHandler | RawMessageHandler | None:
        if isinstance(msg, IncomingMessage):
            item_type = msg.item_type
            if item_type == MessageItemType.TEXT and self._text_handler is not None:
                return self._text_handler
            if item_type == MessageItemType.IMAGE and self._image_handler is not None:
                return self._image_handler
            if item_type == MessageItemType.VOICE and self._voice_handler is not None:
                return self._voice_handler
            if item_type == MessageItemType.FILE and self._file_handler is not None:
                return self._file_handler
            if item_type == MessageItemType.VIDEO and self._video_handler is not None:
                return self._video_handler
            if item_type in {
                MessageItemType.IMAGE,
                MessageItemType.VOICE,
                MessageItemType.FILE,
                MessageItemType.VIDEO,
            }:
                return self._media_handler or self._message_handler
            return self._message_handler

        if self._raw_message_handler is not None:
            return self._raw_message_handler
        handler = self._message_handler
        if handler is None and msg.media_items() and self._media_handler is not None:
            handler = self._media_handler
        if handler is None:
            handler = self._text_handler
        return handler
