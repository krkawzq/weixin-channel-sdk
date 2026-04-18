"""Exception types for weixin_channel."""

from __future__ import annotations


class WeixinChannelError(Exception):
    """Base exception for the Weixin channel SDK."""


class WeixinApiError(WeixinChannelError):
    """Raised when an iLink HTTP API request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
        errcode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.errcode = errcode


class WeixinLoginError(WeixinChannelError):
    """Raised when QR-code login fails or times out."""


class WeixinSessionExpired(WeixinApiError):
    """Raised when the bot token/session is expired."""


class WeixinConfigError(WeixinChannelError):
    """Raised for invalid local configuration or state."""


class WeixinProtocolError(WeixinChannelError):
    """Raised when the API returns an unexpected payload."""
