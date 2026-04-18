"""Access policy helpers for developer-facing bots."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .models import WeixinMessage


@dataclass(slots=True)
class RateLimit:
    """Simple per-key sliding-window rate limit."""

    max_events: int
    window_seconds: float = 60.0
    _events: dict[str, list[float]] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        if self.max_events <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.window_seconds
        events = [ts for ts in self._events.get(key, []) if ts >= cutoff]
        if len(events) >= self.max_events:
            self._events[key] = events
            return False
        events.append(now)
        self._events[key] = events
        return True


@dataclass(slots=True)
class AccessPolicy:
    """Pure SDK access policy.

    This intentionally does not depend on OpenClaw pairing or routing. It only
    checks IDs and simple trigger rules before a message reaches user code.
    """

    allow_users: set[str] | None = None
    allow_groups: set[str] | None = None
    admin_users: set[str] = field(default_factory=set)
    group_enabled: bool = False
    group_trigger_prefixes: tuple[str, ...] = ("/",)
    group_trigger_keywords: tuple[str, ...] = ()
    user_rate_limit: RateLimit | None = None
    group_rate_limit: RateLimit | None = None

    def is_admin(self, msg: WeixinMessage) -> bool:
        return msg.sender_id in self.admin_users

    def allow(self, msg: WeixinMessage) -> bool:
        if msg.is_group_message:
            if not self.group_enabled:
                return False
            if self.allow_groups is not None and msg.group_id not in self.allow_groups:
                return False
            text = msg.text().strip()
            triggered_by_prefix = bool(self.group_trigger_prefixes and text.startswith(self.group_trigger_prefixes))
            triggered_by_keyword = bool(self.group_trigger_keywords and any(key in text for key in self.group_trigger_keywords))
            if (self.group_trigger_prefixes or self.group_trigger_keywords) and not (
                triggered_by_prefix or triggered_by_keyword
            ):
                return False
            if self.group_rate_limit and not self.group_rate_limit.allow(msg.group_id or ""):
                return False

        if self.allow_users is not None and msg.sender_id not in self.allow_users:
            return False

        if self.user_rate_limit and not self.user_rate_limit.allow(msg.sender_id):
            return False

        return True


def strip_group_trigger(text: str, *, prefixes: tuple[str, ...] = ("/",), keywords: tuple[str, ...] = ()) -> str:
    """Best-effort helper for removing a configured group trigger from text."""
    result = text.strip()
    for prefix in prefixes:
        if prefix and result.startswith(prefix):
            return result[len(prefix) :].strip()
    for keyword in keywords:
        if keyword and keyword in result:
            return result.replace(keyword, "", 1).strip()
    return result
