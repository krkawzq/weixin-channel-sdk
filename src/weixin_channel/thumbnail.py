"""Optional thumbnail generation helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .mime import guess_mime
from .utils import generate_client_id


def make_thumbnail(
    file_path: str | Path,
    *,
    dest_dir: str | Path,
    size: tuple[int, int] = (320, 320),
) -> Path | None:
    """Best-effort thumbnail generation.

    Images use optional Pillow. Videos use optional ffmpeg. Returns None when
    the required optional tool is unavailable.
    """
    src = Path(file_path)
    mime = guess_mime(src.name)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    if mime.startswith("image/"):
        try:
            from PIL import Image
        except ModuleNotFoundError:
            return None
        output = dest / f"{generate_client_id('thumb').replace(':', '-')}.jpg"
        with Image.open(src) as img:
            img.thumbnail(size)
            if img.mode not in {"RGB", "L"}:
                img = img.convert("RGB")
            img.save(output, "JPEG", quality=85)
        return output

    if mime.startswith("video/"):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        output = dest / f"{generate_client_id('thumb').replace(':', '-')}.jpg"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-ss",
                "00:00:01",
                "-frames:v",
                "1",
                "-vf",
                f"scale='min({size[0]},iw)':-2",
                str(output),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return output if output.exists() else None

    return None
