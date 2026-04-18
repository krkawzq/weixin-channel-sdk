"""Small utility helpers."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


def random_wechat_uin() -> str:
    """Generate X-WECHAT-UIN: random uint32 -> decimal string -> base64."""
    uint32 = int.from_bytes(secrets.token_bytes(4), byteorder="big", signed=False)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")


def generate_client_id(prefix: str = "weixin-channel") -> str:
    return f"{prefix}:{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_base_url(url: str) -> str:
    return url.rstrip("/")


def default_state_dir() -> Path:
    env = os.environ.get("WEIXIN_CHANNEL_STATE_DIR") or os.environ.get("OPENCLAW_STATE_DIR")
    if env and env.strip():
        return Path(env).expanduser()
    return Path.home() / ".weixin-channel"


def safe_key(raw: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw.strip())
    return safe or "default"


def safe_filename(raw: str, fallback: str = "file.bin") -> str:
    cleaned = raw.replace("\\", "_").replace("/", "_").replace("\x00", "")
    cleaned = "".join(ch if ch not in '<>:"|?*' else "_" for ch in cleaned).strip()
    cleaned = cleaned.strip(". ")
    return cleaned or fallback
