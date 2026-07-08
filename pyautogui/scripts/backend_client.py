"""Client for pushing clean articles into opportunity-radar-agent backend."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"


def compute_content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def post_clean_article(
    title: str,
    content: str,
    source: str = "公众号自动采集",
    author: str = "",
    published_at: str = "",
    url: str = "",
    metadata: dict | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> dict:
    endpoint = backend_url.rstrip("/") + "/articles/ingest-clean"

    payload = {
        "title": title,
        "source": source,
        "author": author,
        "published_at": published_at,
        "content": content,
        "url": url,
        "content_hash": compute_content_hash(content),
        "metadata": metadata or {},
    }

    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Backend HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Backend request failed: {exc.reason}") from exc


def post_clean_article_file(
    clean_text_path: str | Path,
    title: str = "",
    source: str = "公众号自动采集",
    backend_url: str = DEFAULT_BACKEND_URL,
) -> dict:
    path = Path(clean_text_path)
    content = path.read_text(encoding="utf-8")

    if not title:
        for line in content.splitlines():
            if line.strip():
                title = line.strip()
                break

    return post_clean_article(
        title=title,
        content=content,
        source=source,
        metadata={
            "clean_text_path": str(path),
            "collector": "pyautogui_clipboard",
        },
        backend_url=backend_url,
    )
