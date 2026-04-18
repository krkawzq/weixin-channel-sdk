"""Async HTTP wrapper for Weixin ClawBot/iLink APIs."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote

import httpx

from .errors import WeixinApiError, WeixinSessionExpired
from .models import (
    GetConfigResponse,
    GetUploadUrlResponse,
    GetUpdatesResponse,
    JsonDict,
    QrCodeResponse,
    QrStatusResponse,
    TypingStatus,
)
from .utils import ensure_base_url, json_dumps_compact, random_wechat_uin

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_BOT_TYPE = "3"
DEFAULT_CHANNEL_VERSION = "1.0.2"
DEFAULT_ILINK_APP_ID = "bot"
DEFAULT_ILINK_CLIENT_VERSION_STR = "2.1.8"
SESSION_EXPIRED_ERRCODE = -14


def encode_client_version(version: str) -> int:
    """Encode a dotted iLink client version into the integer header value."""
    parts = (version.split(".") + ["0", "0", "0"])[:3]
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        major = minor = patch = 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


class WeixinApi:
    """Low-level async iLink API client."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
        route_tag: str | None = None,
        channel_version: str = DEFAULT_CHANNEL_VERSION,
        ilink_app_id: str = DEFAULT_ILINK_APP_ID,
        ilink_client_version: int | str = encode_client_version(DEFAULT_ILINK_CLIENT_VERSION_STR),
        timeout: float = 15.0,
        long_poll_timeout: float = 38.0,
        http_client: httpx.AsyncClient | None = None,
        trust_env: bool = True,
    ) -> None:
        self.base_url = ensure_base_url(base_url)
        self.token = token
        self.route_tag = route_tag
        self.channel_version = channel_version
        self.ilink_app_id = ilink_app_id
        self.ilink_client_version = str(ilink_client_version)
        self.timeout = timeout
        self.long_poll_timeout = long_poll_timeout
        self.trust_env = trust_env
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> "WeixinApi":
        self._ensure_client()
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                trust_env=self.trust_env,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    def _url(self, path: str, *, base_url: str | None = None) -> str:
        return f"{ensure_base_url(base_url or self.base_url)}/{path.lstrip('/')}"

    def _common_headers(self) -> dict[str, str]:
        headers = {
            "iLink-App-Id": self.ilink_app_id,
            "iLink-App-ClientVersion": self.ilink_client_version,
        }
        if self.route_tag:
            headers["SKRouteTag"] = self.route_tag
        return headers

    def _headers(self, body: str | None = None, *, token: str | None = None) -> dict[str, str]:
        headers = {
            **self._common_headers(),
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": random_wechat_uin(),
        }
        if body is not None:
            headers["Content-Length"] = str(len(body.encode("utf-8")))
        resolved_token = token if token is not None else self.token
        if resolved_token:
            headers["Authorization"] = f"Bearer {resolved_token.strip()}"
        return headers

    def _with_base_info(self, body: JsonDict) -> JsonDict:
        return _strip_none({**body, "base_info": {"channel_version": self.channel_version}})

    async def _get_json(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        treat_timeout_as_empty: bool = False,
        base_url: str | None = None,
    ) -> JsonDict:
        client = self._ensure_client()
        resolved_headers = {**self._common_headers(), **(headers or {})}
        try:
            res = await client.get(
                self._url(path, base_url=base_url),
                headers=resolved_headers,
                timeout=timeout or self.timeout,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError):
            if treat_timeout_as_empty:
                return {}
            raise
        except httpx.HTTPError as exc:
            raise WeixinApiError(f"GET {path} failed: {exc}") from exc
        text = res.text
        if not res.is_success:
            raise WeixinApiError(
                f"GET {path} failed with HTTP {res.status_code}",
                status_code=res.status_code,
                response_text=text,
            )
        try:
            data = res.json()
        except json.JSONDecodeError as exc:
            raise WeixinApiError(
                f"GET {path} returned invalid JSON",
                status_code=res.status_code,
                response_text=text,
            ) from exc
        if not isinstance(data, dict):
            raise WeixinApiError(f"GET {path} returned non-object JSON")
        return data

    async def _post_json(
        self,
        path: str,
        body: JsonDict,
        *,
        timeout: float | None = None,
        treat_timeout_as_empty: bool = False,
        raise_on_ret_error: bool = True,
    ) -> JsonDict:
        payload = self._with_base_info(body)
        body_text = json_dumps_compact(payload)
        client = self._ensure_client()
        try:
            res = await client.post(
                self._url(path),
                content=body_text,
                headers=self._headers(body_text),
                timeout=timeout or self.timeout,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError):
            if treat_timeout_as_empty:
                return {}
            raise
        except httpx.HTTPError as exc:
            raise WeixinApiError(f"POST {path} failed: {exc}") from exc

        text = res.text
        if not res.is_success:
            raise WeixinApiError(
                f"POST {path} failed with HTTP {res.status_code}",
                status_code=res.status_code,
                response_text=text,
            )
        if not text:
            return {}
        try:
            data = res.json()
        except json.JSONDecodeError as exc:
            raise WeixinApiError(
                f"POST {path} returned invalid JSON",
                status_code=res.status_code,
                response_text=text,
            ) from exc
        if not isinstance(data, dict):
            raise WeixinApiError(f"POST {path} returned non-object JSON")
        ret = data.get("ret")
        explicit_errcode = data.get("errcode")
        if ret == SESSION_EXPIRED_ERRCODE or explicit_errcode == SESSION_EXPIRED_ERRCODE:
            raise WeixinSessionExpired(
                "Weixin bot session expired",
                errcode=SESSION_EXPIRED_ERRCODE,
                response_text=text,
            )
        if raise_on_ret_error:
            if (isinstance(ret, int) and ret != 0) or (
                isinstance(explicit_errcode, int) and explicit_errcode != 0
            ):
                error_code = (
                    explicit_errcode
                    if isinstance(explicit_errcode, int) and explicit_errcode != 0
                    else ret
                )
                raise WeixinApiError(
                    f"POST {path} returned API error ret={ret} errcode={explicit_errcode}",
                    errcode=error_code if isinstance(error_code, int) else None,
                    response_text=text,
                )
        return data

    async def get_bot_qrcode(self, *, bot_type: str = DEFAULT_BOT_TYPE) -> QrCodeResponse:
        data = await self._get_json(
            f"ilink/bot/get_bot_qrcode?bot_type={quote(bot_type, safe='')}",
        )
        return QrCodeResponse.model_validate(data)

    async def get_qrcode_status(
        self,
        qrcode: str,
        *,
        base_url: str | None = None,
    ) -> QrStatusResponse:
        data = await self._get_json(
            f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}",
            timeout=self.long_poll_timeout,
            treat_timeout_as_empty=True,
            base_url=base_url,
        )
        if not data:
            return QrStatusResponse(status="wait")
        return QrStatusResponse.model_validate(data)

    async def get_updates(self, cursor: str = "") -> GetUpdatesResponse:
        data = await self._post_json(
            "ilink/bot/getupdates",
            {"get_updates_buf": cursor},
            timeout=self.long_poll_timeout,
            treat_timeout_as_empty=True,
            raise_on_ret_error=False,
        )
        if not data:
            return GetUpdatesResponse(ret=0, msgs=[], get_updates_buf=cursor)
        resp = GetUpdatesResponse.model_validate(data)
        error_code = resp.error_code()
        if error_code is not None:
            raise WeixinApiError(
                f"POST ilink/bot/getupdates returned API error ret={resp.ret} errcode={resp.errcode}",
                errcode=error_code,
                response_text=json_dumps_compact(data),
            )
        return resp

    async def send_message(self, body: JsonDict) -> None:
        await self._post_json("ilink/bot/sendmessage", body, timeout=self.timeout)

    async def get_upload_url(
        self,
        *,
        filekey: str,
        media_type: int,
        to_user_id: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey: str,
        thumb_rawsize: int | None = None,
        thumb_rawfilemd5: str | None = None,
        thumb_filesize: int | None = None,
        no_need_thumb: bool = True,
    ) -> GetUploadUrlResponse:
        data = await self._post_json(
            "ilink/bot/getuploadurl",
            {
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": rawsize,
                "rawfilemd5": rawfilemd5,
                "filesize": filesize,
                "thumb_rawsize": thumb_rawsize,
                "thumb_rawfilemd5": thumb_rawfilemd5,
                "thumb_filesize": thumb_filesize,
                "no_need_thumb": no_need_thumb,
                "aeskey": aeskey,
            },
            timeout=self.timeout,
        )
        return GetUploadUrlResponse.model_validate(data)

    async def send_text(
        self,
        *,
        to_user_id: str,
        text: str,
        context_token: str,
        client_id: str,
    ) -> None:
        if not context_token:
            raise ValueError("context_token is required for Weixin replies")
        await self.send_message(
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                }
            }
        )

    async def get_config(
        self,
        *,
        ilink_user_id: str,
        context_token: str | None = None,
    ) -> GetConfigResponse:
        data = await self._post_json(
            "ilink/bot/getconfig",
            {"ilink_user_id": ilink_user_id, "context_token": context_token},
            timeout=10.0,
        )
        return GetConfigResponse.model_validate(data)

    async def send_typing(
        self,
        *,
        ilink_user_id: str,
        typing_ticket: str,
        status: TypingStatus = TypingStatus.TYPING,
    ) -> None:
        await self._post_json(
            "ilink/bot/sendtyping",
            {
                "ilink_user_id": ilink_user_id,
                "typing_ticket": typing_ticket,
                "status": int(status),
            },
            timeout=10.0,
        )


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value
