"""Protocol models for the Weixin ClawBot/iLink channel."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageType(IntEnum):
    NONE = 0
    USER = 1
    BOT = 2


class MessageState(IntEnum):
    NEW = 0
    GENERATING = 1
    FINISH = 2


class MessageItemType(IntEnum):
    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class UploadMediaType(IntEnum):
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class TypingStatus(IntEnum):
    TYPING = 1
    CANCEL = 2


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class BaseInfo(_Model):
    channel_version: str | None = None


class TextItem(_Model):
    text: str | None = None


class CDNMedia(_Model):
    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None
    full_url: str | None = None


class ImageItem(_Model):
    media: CDNMedia | None = None
    thumb_media: CDNMedia | None = None
    aeskey: str | None = None
    url: str | None = None
    mid_size: int | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None
    hd_size: int | None = None


class VoiceItem(_Model):
    media: CDNMedia | None = None
    encode_type: int | None = None
    bits_per_sample: int | None = None
    sample_rate: int | None = None
    playtime: int | None = None
    text: str | None = None


class FileItem(_Model):
    media: CDNMedia | None = None
    file_name: str | None = None
    md5: str | None = None
    len: str | None = None


class VideoItem(_Model):
    media: CDNMedia | None = None
    video_size: int | None = None
    play_length: int | None = None
    video_md5: str | None = None
    thumb_media: CDNMedia | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None


class RefMessage(_Model):
    message_item: "MessageItem | None" = None
    title: str | None = None


class MessageItem(_Model):
    type: int | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    is_completed: bool | None = None
    msg_id: str | None = None
    ref_msg: RefMessage | None = None
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    voice_item: VoiceItem | None = None
    file_item: FileItem | None = None
    video_item: VideoItem | None = None

    def item_type(self) -> MessageItemType | None:
        if self.type is None:
            return None
        try:
            return MessageItemType(self.type)
        except ValueError:
            return None


class WeixinMessage(_Model):
    seq: int | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    to_user_id: str | None = None
    client_id: str | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    delete_time_ms: int | None = None
    session_id: str | None = None
    group_id: str | None = None
    message_type: int | None = None
    message_state: int | None = None
    item_list: list[MessageItem] = Field(default_factory=list)
    context_token: str | None = None

    @property
    def is_user_message(self) -> bool:
        return self.message_type == MessageType.USER

    @property
    def is_bot_message(self) -> bool:
        return self.message_type == MessageType.BOT

    @property
    def sender_id(self) -> str:
        return self.from_user_id or ""

    @property
    def is_group_message(self) -> bool:
        return bool(self.group_id)

    @property
    def conversation_id(self) -> str:
        return self.group_id or self.from_user_id or self.session_id or ""

    def media_items(self) -> list[MessageItem]:
        return [
            item
            for item in self.item_list
            if item.item_type()
            in {
                MessageItemType.IMAGE,
                MessageItemType.VOICE,
                MessageItemType.FILE,
                MessageItemType.VIDEO,
            }
        ]

    def text(self) -> str:
        """Extract a useful text body from the message.

        Mirrors the TypeScript plugin's behavior for the text path:
        - direct text item
        - voice transcription
        - simple placeholders for media-only messages
        """
        for item in self.item_list:
            item_type = item.item_type()
            if item_type == MessageItemType.TEXT and item.text_item and item.text_item.text:
                return item.text_item.text
            if item_type == MessageItemType.VOICE and item.voice_item and item.voice_item.text:
                return item.voice_item.text
        for item in self.item_list:
            item_type = item.item_type()
            if item_type == MessageItemType.IMAGE:
                return "[图片]"
            if item_type == MessageItemType.FILE:
                name = item.file_item.file_name if item.file_item else ""
                return f"[文件] {name}".strip()
            if item_type == MessageItemType.VIDEO:
                return "[视频]"
            if item_type == MessageItemType.VOICE:
                return "[语音]"
        return ""


class GetUpdatesResponse(_Model):
    ret: int | None = None
    errcode: int | None = None
    errmsg: str | None = None
    msgs: list[WeixinMessage] = Field(default_factory=list)
    sync_buf: str | None = None
    get_updates_buf: str | None = None
    longpolling_timeout_ms: int | None = None

    def error_code(self) -> int | None:
        for value in (self.errcode, self.ret):
            if isinstance(value, int) and value != 0:
                return value
        return None

    def is_ok(self) -> bool:
        return self.error_code() is None


class QrCodeResponse(_Model):
    qrcode: str
    qrcode_img_content: str


class QrStatusResponse(_Model):
    status: Literal["wait", "scaned", "scaned_but_redirect", "confirmed", "expired"]
    bot_token: str | None = None
    ilink_bot_id: str | None = None
    baseurl: str | None = None
    ilink_user_id: str | None = None
    redirect_host: str | None = None


class GetConfigResponse(_Model):
    ret: int | None = None
    errmsg: str | None = None
    typing_ticket: str | None = None


class GetUploadUrlResponse(_Model):
    upload_param: str | None = None
    upload_full_url: str | None = None
    thumb_upload_param: str | None = None
    thumb_upload_full_url: str | None = None


class UploadedMedia(_Model):
    filekey: str
    download_encrypted_query_param: str
    aeskey_hex: str
    file_size: int
    file_size_ciphertext: int
    media_type: UploadMediaType
    file_name: str | None = None
    mime_type: str | None = None
    thumb_download_encrypted_query_param: str | None = None
    thumb_aeskey_hex: str | None = None
    thumb_size: int | None = None
    thumb_size_ciphertext: int | None = None


class DownloadedMedia(_Model):
    path: str
    mime_type: str | None = None
    file_name: str | None = None
    item_type: MessageItemType | None = None


class AccountSession(_Model):
    token: str
    base_url: str = "https://ilinkai.weixin.qq.com"
    account_id: str | None = None
    user_id: str | None = None
    saved_at: str | None = None

    @classmethod
    def create(
        cls,
        *,
        token: str,
        base_url: str | None = None,
        account_id: str | None = None,
        user_id: str | None = None,
    ) -> "AccountSession":
        return cls(
            token=token,
            base_url=base_url or "https://ilinkai.weixin.qq.com",
            account_id=account_id,
            user_id=user_id,
            saved_at=datetime.now(timezone.utc).isoformat(),
        )


JsonDict = dict[str, Any]
