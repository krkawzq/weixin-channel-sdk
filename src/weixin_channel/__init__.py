"""Python SDK for the Weixin ClawBot/iLink channel."""

from .api import (
    DEFAULT_BASE_URL,
    DEFAULT_BOT_TYPE,
    DEFAULT_CDN_BASE_URL,
    DEFAULT_CHANNEL_VERSION,
    WeixinApi,
)
from .bot import WeixinBot
from .events import BotEvent
from .client import LoginEvent, WeixinClient, terminal_qr_renderer
from .config import ConcurrencyConfig, MediaConfig, RetryConfig, SessionGuardConfig
from .errors import (
    WeixinApiError,
    WeixinChannelError,
    WeixinConfigError,
    WeixinLoginError,
    WeixinProtocolError,
    WeixinSessionExpired,
)
from .models import (
    AccountSession,
    CDNMedia,
    DownloadedMedia,
    FileItem,
    GetConfigResponse,
    GetUploadUrlResponse,
    GetUpdatesResponse,
    ImageItem,
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    QrCodeResponse,
    QrStatusResponse,
    TextItem,
    TypingStatus,
    UploadedMedia,
    UploadMediaType,
    VideoItem,
    VoiceItem,
    WeixinMessage,
)
from .store import StateStore
from .policy import AccessPolicy, RateLimit, strip_group_trigger
from .sync import SyncWeixinClient
from .text import markdown_to_plain_text
from .thumbnail import make_thumbnail
from .logging import JsonlEventWriter, get_logger, redact
from .cdn import (
    VoiceConverter,
    build_media_message_item,
    build_cdn_download_url,
    build_cdn_upload_url,
    download_media_item,
    download_remote_file,
    media_type_for_file,
    upload_media_file,
)
from .crypto import (
    aes_ecb_padded_size,
    decrypt_aes_128_ecb,
    encrypt_aes_128_ecb,
    parse_cdn_aes_key,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AccountSession",
    "CDNMedia",
    "ConcurrencyConfig",
    "DEFAULT_BASE_URL",
    "DEFAULT_BOT_TYPE",
    "DEFAULT_CDN_BASE_URL",
    "DEFAULT_CHANNEL_VERSION",
    "DownloadedMedia",
    "FileItem",
    "GetConfigResponse",
    "GetUploadUrlResponse",
    "GetUpdatesResponse",
    "ImageItem",
    "LoginEvent",
    "MediaConfig",
    "MessageItem",
    "MessageItemType",
    "MessageState",
    "MessageType",
    "QrCodeResponse",
    "QrStatusResponse",
    "StateStore",
    "AccessPolicy",
    "BotEvent",
    "RateLimit",
    "RetryConfig",
    "SessionGuardConfig",
    "TextItem",
    "TypingStatus",
    "UploadedMedia",
    "UploadMediaType",
    "VideoItem",
    "VoiceItem",
    "WeixinApi",
    "WeixinApiError",
    "WeixinBot",
    "WeixinChannelError",
    "WeixinClient",
    "SyncWeixinClient",
    "WeixinConfigError",
    "WeixinLoginError",
    "WeixinMessage",
    "WeixinProtocolError",
    "WeixinSessionExpired",
    "terminal_qr_renderer",
    "JsonlEventWriter",
    "VoiceConverter",
    "aes_ecb_padded_size",
    "build_cdn_download_url",
    "build_cdn_upload_url",
    "build_media_message_item",
    "decrypt_aes_128_ecb",
    "download_media_item",
    "download_remote_file",
    "encrypt_aes_128_ecb",
    "media_type_for_file",
    "parse_cdn_aes_key",
    "upload_media_file",
    "markdown_to_plain_text",
    "make_thumbnail",
    "get_logger",
    "redact",
    "strip_group_trigger",
]
