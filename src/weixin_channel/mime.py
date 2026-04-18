"""MIME helpers for media routing."""

from __future__ import annotations

import mimetypes
from pathlib import Path

MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


def guess_mime(path_or_name: str) -> str:
    guessed, _ = mimetypes.guess_type(path_or_name)
    return guessed or "application/octet-stream"


def extension_for_mime(mime_type: str | None, fallback_name: str | None = None) -> str:
    if mime_type:
        ext = MIME_TO_EXTENSION.get(mime_type.split(";", 1)[0].strip().lower())
        if ext:
            return ext
    if fallback_name:
        ext = Path(fallback_name).suffix
        if ext:
            return ext
    return ".bin"
