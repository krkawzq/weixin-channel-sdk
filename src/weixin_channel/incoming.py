"""Developer-friendly item-level inbound message wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .cdn import VoiceConverter, download_media_item
from .models import DownloadedMedia, MessageItem, MessageItemType, WeixinMessage

if TYPE_CHECKING:
    from .client import WeixinClient


@dataclass(slots=True)
class IncomingMessage:
    """One inbound message item with convenience reply/download helpers."""

    client: WeixinClient
    raw_message: WeixinMessage
    item: MessageItem

    @property
    def from_user(self) -> str:
        return self.raw_message.sender_id

    @property
    def sender_id(self) -> str:
        return self.raw_message.sender_id

    @property
    def group_id(self) -> str | None:
        return self.raw_message.group_id

    @property
    def conversation_id(self) -> str:
        return self.raw_message.conversation_id

    @property
    def context_token(self) -> str:
        return self.raw_message.context_token or ""

    @property
    def message_id(self) -> int | None:
        return self.raw_message.message_id

    @property
    def item_type(self) -> MessageItemType | None:
        return self.item.item_type()

    @property
    def is_text(self) -> bool:
        return self.item_type == MessageItemType.TEXT

    @property
    def is_image(self) -> bool:
        return self.item_type == MessageItemType.IMAGE

    @property
    def is_voice(self) -> bool:
        return self.item_type == MessageItemType.VOICE

    @property
    def is_file(self) -> bool:
        return self.item_type == MessageItemType.FILE

    @property
    def is_video(self) -> bool:
        return self.item_type == MessageItemType.VIDEO

    @property
    def text(self) -> str | None:
        if self.is_text and self.item.text_item:
            return self.item.text_item.text
        if self.is_voice and self.item.voice_item:
            return self.item.voice_item.text
        return None

    @property
    def file_name(self) -> str | None:
        if self.is_file and self.item.file_item:
            return self.item.file_item.file_name
        return None

    @property
    def voice_duration_ms(self) -> int:
        if self.is_voice and self.item.voice_item and self.item.voice_item.playtime:
            return int(self.item.voice_item.playtime)
        return 0

    async def reply_text(self, text: str) -> str:
        return await self.client.reply_text(self.raw_message, text)

    async def reply_markdown(self, markdown: str) -> str:
        return await self.client.reply_markdown(self.raw_message, markdown)

    async def reply_image(
        self,
        path: str | Path,
        *,
        caption: str = "",
    ) -> str:
        return await self.client.reply_media_file(self.raw_message, path, text=caption)

    async def reply_video(
        self,
        path: str | Path,
        *,
        caption: str = "",
    ) -> str:
        return await self.client.reply_media_file(self.raw_message, path, text=caption)

    async def reply_file(
        self,
        path: str | Path,
        *,
        caption: str = "",
    ) -> str:
        return await self.client.reply_media_file(self.raw_message, path, text=caption)

    async def reply_remote_media(
        self,
        url: str,
        *,
        caption: str = "",
        download_dir: str | Path | None = None,
    ) -> str:
        return await self.client.reply_remote_media(
            self.raw_message,
            url,
            text=caption,
            download_dir=download_dir,
        )

    async def reply_typing(self) -> None:
        await self.client.send_typing(self.raw_message)

    async def download(
        self,
        *,
        dest_dir: str | Path | None = None,
        voice_converter: VoiceConverter | None = None,
    ) -> DownloadedMedia | None:
        target_dir = Path(dest_dir) if dest_dir is not None else self.client.store.root / "downloads"
        return await download_media_item(
            self.item,
            dest_dir=target_dir,
            cdn_base_url=self.client.cdn_base_url,
            http_client=self.client.api._ensure_client(),
            voice_converter=voice_converter,
            max_bytes=self.client.media.max_download_bytes,
        )

    async def save(
        self,
        path: str | Path,
        *,
        voice_converter: VoiceConverter | None = None,
    ) -> Path:
        target = Path(path)
        downloaded = await self.download(dest_dir=target.parent, voice_converter=voice_converter)
        if downloaded is None:
            raise ValueError(f"message item type {self.item.type!r} is not downloadable")
        source = Path(downloaded.path)
        if source != target:
            source.replace(target)
        return target
