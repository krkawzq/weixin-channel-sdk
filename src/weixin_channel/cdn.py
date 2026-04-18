"""CDN upload/download helpers for Weixin media."""

from __future__ import annotations

import hashlib
import inspect
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

import httpx

from .api import DEFAULT_CDN_BASE_URL, WeixinApi
from .crypto import (
    aes_ecb_padded_size,
    decrypt_aes_128_ecb,
    encode_hex_key_for_cdn,
    encrypt_aes_128_ecb,
    parse_cdn_aes_key,
)
from .mime import extension_for_mime, guess_mime
from .models import DownloadedMedia, MessageItem, MessageItemType, UploadedMedia, UploadMediaType
from .utils import generate_client_id, safe_filename

VoiceConverter = Callable[[bytes], bytes | Awaitable[bytes]]


def build_cdn_download_url(encrypted_query_param: str, cdn_base_url: str = DEFAULT_CDN_BASE_URL) -> str:
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def resolve_cdn_download_url(
    *,
    encrypted_query_param: str | None = None,
    full_url: str | None = None,
    cdn_base_url: str = DEFAULT_CDN_BASE_URL,
) -> str | None:
    if full_url and full_url.strip():
        return full_url.strip()
    if encrypted_query_param:
        return build_cdn_download_url(encrypted_query_param, cdn_base_url)
    return None


def build_cdn_upload_url(
    *,
    upload_param: str,
    filekey: str,
    cdn_base_url: str = DEFAULT_CDN_BASE_URL,
) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload?"
        f"encrypted_query_param={quote(upload_param, safe='')}&filekey={quote(filekey, safe='')}"
    )


async def upload_buffer_to_cdn(
    *,
    buffer: bytes,
    upload_param: str | None = None,
    upload_full_url: str | None = None,
    filekey: str,
    aeskey: bytes,
    cdn_base_url: str = DEFAULT_CDN_BASE_URL,
    http_client: httpx.AsyncClient | None = None,
    max_retries: int = 3,
) -> str:
    ciphertext = encrypt_aes_128_ecb(buffer, aeskey)
    if upload_full_url and upload_full_url.strip():
        url = upload_full_url.strip()
    elif upload_param:
        url = build_cdn_upload_url(upload_param=upload_param, filekey=filekey, cdn_base_url=cdn_base_url)
    else:
        raise RuntimeError("CDN upload URL missing: need upload_full_url or upload_param")
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                res = await client.post(
                    url,
                    content=ciphertext,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=60.0,
                )
                if 400 <= res.status_code < 500:
                    raise RuntimeError(f"CDN upload client error {res.status_code}: {res.text}")
                if res.status_code != 200:
                    raise RuntimeError(f"CDN upload server error {res.status_code}: {res.text}")
                download_param = res.headers.get("x-encrypted-param")
                if not download_param:
                    raise RuntimeError("CDN upload response missing x-encrypted-param")
                return download_param
            except Exception as exc:  # noqa: BLE001
                last_error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                if attempt == max_retries - 1:
                    raise
        assert last_error is not None
        raise last_error
    finally:
        if owns_client:
            await client.aclose()


async def upload_media_file(
    *,
    api: WeixinApi,
    file_path: str | Path,
    to_user_id: str,
    media_type: UploadMediaType,
    cdn_base_url: str = DEFAULT_CDN_BASE_URL,
    http_client: httpx.AsyncClient | None = None,
    thumb_path: str | Path | None = None,
) -> UploadedMedia:
    path = Path(file_path)
    data = path.read_bytes()
    rawsize = len(data)
    rawfilemd5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    ciphertext_size = aes_ecb_padded_size(rawsize)
    filekey = secrets.token_hex(16)
    aeskey = secrets.token_bytes(16)
    aeskey_hex = aeskey.hex()
    mime_type = guess_mime(path.name)

    thumb_data: bytes | None = None
    thumb_rawsize: int | None = None
    thumb_rawfilemd5: str | None = None
    thumb_ciphertext_size: int | None = None
    if thumb_path is not None:
        thumb_data = Path(thumb_path).read_bytes()
        thumb_rawsize = len(thumb_data)
        thumb_rawfilemd5 = hashlib.md5(thumb_data, usedforsecurity=False).hexdigest()
        thumb_ciphertext_size = aes_ecb_padded_size(thumb_rawsize)

    upload_url = await api.get_upload_url(
        filekey=filekey,
        media_type=int(media_type),
        to_user_id=to_user_id,
        rawsize=rawsize,
        rawfilemd5=rawfilemd5,
        filesize=ciphertext_size,
        thumb_rawsize=thumb_rawsize,
        thumb_rawfilemd5=thumb_rawfilemd5,
        thumb_filesize=thumb_ciphertext_size,
        no_need_thumb=thumb_data is None,
        aeskey=aeskey_hex,
    )
    if not upload_url.upload_full_url and not upload_url.upload_param:
        raise RuntimeError("getuploadurl returned no upload URL")

    download_param = await upload_buffer_to_cdn(
        buffer=data,
        upload_param=upload_url.upload_param,
        upload_full_url=upload_url.upload_full_url,
        filekey=filekey,
        aeskey=aeskey,
        cdn_base_url=cdn_base_url,
        http_client=http_client,
    )
    thumb_download_param: str | None = None
    if thumb_data is not None and (upload_url.thumb_upload_full_url or upload_url.thumb_upload_param):
        thumb_download_param = await upload_buffer_to_cdn(
            buffer=thumb_data,
            upload_param=upload_url.thumb_upload_param,
            upload_full_url=upload_url.thumb_upload_full_url,
            filekey=filekey,
            aeskey=aeskey,
            cdn_base_url=cdn_base_url,
            http_client=http_client,
        )
    return UploadedMedia(
        filekey=filekey,
        download_encrypted_query_param=download_param,
        aeskey_hex=aeskey_hex,
        file_size=rawsize,
        file_size_ciphertext=ciphertext_size,
        media_type=media_type,
        file_name=safe_filename(path.name),
        mime_type=mime_type,
        thumb_download_encrypted_query_param=thumb_download_param,
        thumb_aeskey_hex=aeskey_hex if thumb_download_param else None,
        thumb_size=thumb_rawsize,
        thumb_size_ciphertext=thumb_ciphertext_size,
    )


async def download_remote_file(
    url: str,
    *,
    dest_dir: str | Path,
    http_client: httpx.AsyncClient | None = None,
    max_bytes: int | None = None,
) -> Path:
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        chunks: list[bytes] = []
        total = 0
        async with client.stream("GET", url, timeout=60.0) as res:
            res.raise_for_status()
            content_type = res.headers.get("content-type")
            async for chunk in res.aiter_bytes():
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise RuntimeError(f"remote media exceeds max_bytes={max_bytes}")
                chunks.append(chunk)
        ext = extension_for_mime(content_type, url)
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        file_path = dest / f"{generate_client_id('weixin-remote').replace(':', '-')}{ext}"
        file_path.write_bytes(b"".join(chunks))
        return file_path
    finally:
        if owns_client:
            await client.aclose()


def media_type_for_file(path: str | Path) -> UploadMediaType:
    mime = guess_mime(str(path))
    if mime.startswith("image/"):
        return UploadMediaType.IMAGE
    if mime.startswith("video/"):
        return UploadMediaType.VIDEO
    return UploadMediaType.FILE


def build_media_message_item(uploaded: UploadedMedia) -> dict:
    aes_key = encode_hex_key_for_cdn(uploaded.aeskey_hex)
    media = {
        "encrypt_query_param": uploaded.download_encrypted_query_param,
        "aes_key": aes_key,
        "encrypt_type": 1,
    }
    if uploaded.media_type == UploadMediaType.IMAGE:
        thumb_media = (
            {
                "encrypt_query_param": uploaded.thumb_download_encrypted_query_param,
                "aes_key": encode_hex_key_for_cdn(uploaded.thumb_aeskey_hex or uploaded.aeskey_hex),
                "encrypt_type": 1,
            }
            if uploaded.thumb_download_encrypted_query_param
            else None
        )
        return {
            "type": int(MessageItemType.IMAGE),
            "image_item": {
                "media": media,
                **({"thumb_media": thumb_media} if thumb_media else {}),
                "mid_size": uploaded.file_size_ciphertext,
                **({"thumb_size": uploaded.thumb_size_ciphertext} if uploaded.thumb_size_ciphertext else {}),
            },
        }
    if uploaded.media_type == UploadMediaType.VIDEO:
        thumb_media = (
            {
                "encrypt_query_param": uploaded.thumb_download_encrypted_query_param,
                "aes_key": encode_hex_key_for_cdn(uploaded.thumb_aeskey_hex or uploaded.aeskey_hex),
                "encrypt_type": 1,
            }
            if uploaded.thumb_download_encrypted_query_param
            else None
        )
        return {
            "type": int(MessageItemType.VIDEO),
            "video_item": {
                "media": media,
                **({"thumb_media": thumb_media} if thumb_media else {}),
                "video_size": uploaded.file_size_ciphertext,
                **({"thumb_size": uploaded.thumb_size_ciphertext} if uploaded.thumb_size_ciphertext else {}),
            },
        }
    return {
        "type": int(MessageItemType.FILE),
        "file_item": {
            "media": media,
            "file_name": uploaded.file_name or "file.bin",
            "len": str(uploaded.file_size),
        },
    }


def _media_for_item(
    item: MessageItem,
) -> tuple[str | None, str | None, bytes | None, str | None, str | None]:
    item_type = item.item_type()
    if item_type == MessageItemType.IMAGE and item.image_item and item.image_item.media:
        media = item.image_item.media
        key = bytes.fromhex(item.image_item.aeskey) if item.image_item.aeskey else (
            parse_cdn_aes_key(media.aes_key) if media.aes_key else None
        )
        return media.encrypt_query_param, media.full_url, key, None, "image"
    if item_type == MessageItemType.VOICE and item.voice_item and item.voice_item.media:
        media = item.voice_item.media
        key = parse_cdn_aes_key(media.aes_key) if media.aes_key else None
        return media.encrypt_query_param, media.full_url, key, None, "voice"
    if item_type == MessageItemType.FILE and item.file_item and item.file_item.media:
        media = item.file_item.media
        key = parse_cdn_aes_key(media.aes_key) if media.aes_key else None
        return media.encrypt_query_param, media.full_url, key, item.file_item.file_name, "file"
    if item_type == MessageItemType.VIDEO and item.video_item and item.video_item.media:
        media = item.video_item.media
        key = parse_cdn_aes_key(media.aes_key) if media.aes_key else None
        return media.encrypt_query_param, media.full_url, key, None, "video"
    return None, None, None, None, None


async def download_media_item(
    item: MessageItem,
    *,
    dest_dir: str | Path,
    cdn_base_url: str = DEFAULT_CDN_BASE_URL,
    http_client: httpx.AsyncClient | None = None,
    voice_converter: VoiceConverter | None = None,
    max_bytes: int | None = None,
) -> DownloadedMedia | None:
    encrypted_param, full_url, aeskey, file_name, label = _media_for_item(item)
    url = resolve_cdn_download_url(
        encrypted_query_param=encrypted_param,
        full_url=full_url,
        cdn_base_url=cdn_base_url,
    )
    if not url:
        return None

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        chunks: list[bytes] = []
        total = 0
        async with client.stream("GET", url, timeout=60.0) as res:
            res.raise_for_status()
            async for chunk in res.aiter_bytes():
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise RuntimeError(f"inbound media exceeds max_bytes={max_bytes}")
                chunks.append(chunk)
        data = b"".join(chunks)
    finally:
        if owns_client:
            await client.aclose()

    plaintext = decrypt_aes_128_ecb(data, aeskey) if aeskey else data
    item_type = item.item_type()
    if item_type == MessageItemType.VOICE and voice_converter is not None:
        converted = voice_converter(plaintext)
        plaintext = await converted if inspect.isawaitable(converted) else converted
        file_name = None
        label = "voice"
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    mime_type = "audio/wav" if item_type == MessageItemType.VOICE and voice_converter else guess_mime(file_name or "")
    ext = extension_for_mime(None if mime_type == "application/octet-stream" else mime_type, file_name)
    output_name = safe_filename(file_name) if file_name else f"{generate_client_id(label or 'media').replace(':', '-')}{ext}"
    output_path = dest / output_name
    output_path.write_bytes(plaintext)
    return DownloadedMedia(
        path=str(output_path),
        mime_type=mime_type,
        file_name=file_name,
        item_type=item_type,
    )
