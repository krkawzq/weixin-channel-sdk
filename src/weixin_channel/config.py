"""Configuration objects for weixin_channel."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import WeixinConfigError


@dataclass(slots=True)
class RetryConfig:
    retry_delay_s: float = 2.0
    backoff_delay_s: float = 30.0
    max_consecutive_failures: int = 3
    seen_flush_interval: int = 20
    seen_cache_limit: int = 1000

    def __post_init__(self) -> None:
        if self.retry_delay_s < 0:
            raise WeixinConfigError("retry_delay_s must be >= 0")
        if self.backoff_delay_s < 0:
            raise WeixinConfigError("backoff_delay_s must be >= 0")
        if self.max_consecutive_failures < 1:
            raise WeixinConfigError("max_consecutive_failures must be >= 1")
        if self.seen_flush_interval < 1:
            raise WeixinConfigError("seen_flush_interval must be >= 1")
        if self.seen_cache_limit < 1:
            raise WeixinConfigError("seen_cache_limit must be >= 1")


@dataclass(slots=True)
class SessionGuardConfig:
    pause_on_expired: bool = True
    pause_seconds: float = 60 * 60

    def __post_init__(self) -> None:
        if self.pause_seconds < 0:
            raise WeixinConfigError("pause_seconds must be >= 0")


@dataclass(slots=True)
class MediaConfig:
    auto_thumbnail: bool = True
    thumbnail_size: tuple[int, int] = (320, 320)
    max_download_bytes: int = 100 * 1024 * 1024
    max_upload_bytes: int = 100 * 1024 * 1024

    def __post_init__(self) -> None:
        width, height = self.thumbnail_size
        if width < 1 or height < 1:
            raise WeixinConfigError("thumbnail_size dimensions must be >= 1")
        if self.max_download_bytes < 0:
            raise WeixinConfigError("max_download_bytes must be >= 0")
        if self.max_upload_bytes < 0:
            raise WeixinConfigError("max_upload_bytes must be >= 0")


@dataclass(slots=True)
class ConcurrencyConfig:
    mode: str = "serial"  # serial | per-conversation | concurrent
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        if self.mode not in {"serial", "per-conversation", "concurrent"}:
            raise WeixinConfigError("mode must be one of: serial, per-conversation, concurrent")
        if self.max_concurrency < 1:
            raise WeixinConfigError("max_concurrency must be >= 1")
