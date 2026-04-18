"""Lightweight structured logging helpers."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

SECRET_KEYS = {"token", "authorization", "context_token", "aes_key", "aeskey", "encrypt_query_param"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in SECRET_KEYS else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class JsonlEventWriter:
    """Write redacted events to a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, **data: Any) -> None:
        payload = {
            "ts": time.time(),
            "type": event_type,
            **redact(data),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def get_logger(name: str = "weixin_channel") -> logging.Logger:
    return logging.getLogger(name)
