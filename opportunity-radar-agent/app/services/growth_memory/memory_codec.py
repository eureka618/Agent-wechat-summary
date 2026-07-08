from __future__ import annotations

import json
from typing import Any


META_PREFIX = "[[memory_meta:"
META_SUFFIX = "]]"


def encode_memory_summary(summary: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    if not metadata:
        return summary.strip()
    compact = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    return f"{META_PREFIX}{compact}{META_SUFFIX}\n{summary.strip()}"


def decode_memory_summary(value: str | None) -> tuple[str, dict[str, Any]]:
    text = str(value or "").strip()
    if not text.startswith(META_PREFIX):
        return text, {}
    end = text.find(META_SUFFIX)
    if end < 0:
        return text, {}
    raw_meta = text[len(META_PREFIX) : end]
    summary = text[end + len(META_SUFFIX) :].strip()
    try:
        metadata = json.loads(raw_meta)
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return summary, metadata
