"""Configuration objects for weixin_channel."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RetryConfig:
    retry_delay_s: float = 2.0
    backoff_delay_s: float = 30.0
    max_consecutive_failures: int = 3
    seen_flush_interval: int = 20


@dataclass(slots=True)
class SessionGuardConfig:
    pause_on_expired: bool = True
    pause_seconds: float = 60 * 60


@dataclass(slots=True)
class MediaConfig:
    auto_thumbnail: bool = True
    thumbnail_size: tuple[int, int] = (320, 320)
    max_download_bytes: int = 100 * 1024 * 1024
    max_upload_bytes: int = 100 * 1024 * 1024


@dataclass(slots=True)
class ConcurrencyConfig:
    mode: str = "serial"  # serial | per-conversation | concurrent
    max_concurrency: int = 4
