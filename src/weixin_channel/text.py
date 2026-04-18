"""Text formatting helpers."""

from __future__ import annotations

import re


def markdown_to_plain_text(text: str) -> str:
    """Convert common Markdown to Weixin-friendly plain text."""
    result = text
    result = re.sub(r"```[^\n]*\n?([\s\S]*?)```", lambda m: m.group(1).strip(), result)
    result = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", result)
    result = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", result)
    result = re.sub(r"^\|[\s:|-]+\|$", "", result, flags=re.MULTILINE)
    result = re.sub(
        r"^\|(.+)\|$",
        lambda m: "  ".join(cell.strip() for cell in m.group(1).split("|")),
        result,
        flags=re.MULTILINE,
    )
    result = re.sub(r"(^|\s)([*_~`]{1,3})([^*_~`\n]+)\2", r"\1\3", result)
    result = re.sub(r"^#{1,6}\s*", "", result, flags=re.MULTILINE)
    result = re.sub(r"^\s*[-*+]\s+", "- ", result, flags=re.MULTILINE)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
