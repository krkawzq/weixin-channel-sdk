"""Callback runner for WeixinClient."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from .client import WeixinClient
from .config import ConcurrencyConfig
from .events import BotEvent
from .models import WeixinMessage
from .policy import AccessPolicy

TextHandler = Callable[[WeixinMessage], Awaitable[str | None] | str | None]
MessageHandler = Callable[[WeixinMessage], Awaitable[str | None] | str | None]
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
    ) -> None:
        self.client = client
        self.allow_from = allow_from
        self.policy = policy
        self.typing = typing
        self.send_error_notice = send_error_notice
        self.concurrency = concurrency or client.concurrency
        self._semaphore = asyncio.Semaphore(max(1, self.concurrency.max_concurrency))
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._max_pending_tasks = max(1, self.concurrency.max_concurrency * 4)
        self._message_handler: MessageHandler | None = None
        self._text_handler: TextHandler | None = None
        self._media_handler: MessageHandler | None = None
        self._error_handler: ErrorHandler | None = None
        self._event_handler: EventHandler | None = None

    def on_message(self, handler: MessageHandler) -> MessageHandler:
        self._message_handler = handler
        return handler

    def on_text(self, handler: TextHandler) -> TextHandler:
        self._text_handler = handler
        return handler

    def on_media(self, handler: MessageHandler) -> MessageHandler:
        self._media_handler = handler
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
        if self._message_handler is None and self._text_handler is None and self._media_handler is None:
            raise RuntimeError("no handler registered; use @bot.on_message, @bot.on_text, or @bot.on_media")

        async for msg in self.client.poll_messages():
            if self.concurrency.mode == "serial":
                await self._process_message(msg)
                continue
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

    async def _process_message_with_limits(self, msg: WeixinMessage) -> None:
        async with self._semaphore:
            if self.concurrency.mode == "per-conversation":
                lock = self._conversation_locks.setdefault(msg.conversation_id, asyncio.Lock())
                async with lock:
                    await self._process_message(msg)
            else:
                await self._process_message(msg)

    async def _process_message(self, msg: WeixinMessage) -> None:
        await self._emit("message.received", from_user_id=msg.from_user_id, group_id=msg.group_id)
        if not msg.is_user_message:
            await self._emit("message.skipped", "not a user message", reason="not_user")
            return
        if not self._allowed(msg):
            await self._emit("message.skipped", "blocked by policy", reason="policy")
            return
        try:
            handler = self._message_handler
            if handler is None and msg.media_items() and self._media_handler is not None:
                handler = self._media_handler
            if handler is None:
                handler = self._text_handler
            if handler is None:
                return

            await self._emit("handler.start", from_user_id=msg.from_user_id, text=msg.text())
            if self.typing:
                async with self.client.typing(msg):
                    result = await self._maybe_await(handler(msg))
            else:
                result = await self._maybe_await(handler(msg))
            await self._emit("handler.done", from_user_id=msg.from_user_id)

            if isinstance(result, str) and result:
                await self.client.reply_text(msg, result)
                await self._emit("reply.sent", from_user_id=msg.from_user_id)
        except BaseException as exc:
            await self._emit("handler.error", str(exc), error=repr(exc))
            if self.send_error_notice and msg.context_token:
                try:
                    await self.client.reply_text(msg, f"⚠️ 处理失败：{str(exc)[:200]}")
                except Exception:
                    pass
            await self._handle_error(exc)
