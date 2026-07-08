"""Article storage, cleaning, and lightweight deduplication."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Dict, List


def normalize_article_text(text: str) -> str:
    """Lightly clean copied article text.

    Keep the article readable. Do not over-clean.
    """
    if not text:
        return ""

    lines: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        # Remove common placeholder text copied from WeChat articles.
        if line in {"图片", "视频", "音频"}:
            continue

        # Remove excessive invisible characters.
        line = line.replace("\u200b", "").replace("\ufeff", "").strip()

        if not line:
            continue

        lines.append(line)

    text = "\n".join(lines)

    # Collapse too many blank lines, just in case.
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def guess_title(clean_text: str, fallback_title: str = "") -> str:
    """Use the first non-empty line as title."""
    for line in clean_text.splitlines():
        line = line.strip()
        if line:
            return line

    return fallback_title.strip()


def compute_text_hash(text: str) -> str:
    """Compute stable hash for deduplication."""
    normalized = re.sub(r"\s+", "", text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_index(index_path: Path) -> Dict[str, Any]:
    if not index_path.exists():
        return {
            "articles": [],
            "hashes": {},
        }

    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "articles": [],
            "hashes": {},
        }


def save_index(index_path: Path, index_data: Dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_article_with_dedup(
    article_number: int,
    raw_text: str,
    output_dir: str | Path = "output",
    source_title: str = "",
) -> Dict[str, Any]:
    """Save raw/clean/meta files unless the article is duplicated.

    Returns metadata dict.
    """
    output_dir = Path(output_dir)
    article_dir = output_dir / "articles"
    article_dir.mkdir(parents=True, exist_ok=True)

    index_path = article_dir / "index.json"
    index_data = load_index(index_path)

    clean_text = normalize_article_text(raw_text)
    title = guess_title(clean_text, fallback_title=source_title)
    text_hash = compute_text_hash(clean_text)

    now = datetime.now().isoformat(timespec="seconds")

    if text_hash in index_data.get("hashes", {}):
        existing = index_data["hashes"][text_hash]

        meta = {
            "article_number": article_number,
            "title": title,
            "hash": text_hash,
            "duplicate": True,
            "duplicate_of": existing,
            "saved": False,
            "created_at": now,
            "reason": "same_clean_text_hash",
        }

        skipped_meta_path = article_dir / f"article_{article_number}_meta.json"
        skipped_meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"[article_store] duplicate article skipped: {title}")
        return meta

    raw_path = article_dir / f"article_{article_number}_raw.txt"
    clean_path = article_dir / f"article_{article_number}_clean.txt"
    meta_path = article_dir / f"article_{article_number}_meta.json"

    raw_path.write_text(raw_text.strip() + "\n", encoding="utf-8")
    clean_path.write_text(clean_text.strip() + "\n", encoding="utf-8")

    meta = {
        "article_number": article_number,
        "title": title,
        "hash": text_hash,
        "duplicate": False,
        "saved": True,
        "raw_text_path": str(raw_path),
        "clean_text_path": str(clean_path),
        "meta_path": str(meta_path),
        "raw_chars": len(raw_text),
        "clean_chars": len(clean_text),
        "created_at": now,
    }

    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    index_data.setdefault("articles", [])
    index_data.setdefault("hashes", {})

    index_data["articles"].append(meta)
    index_data["hashes"][text_hash] = {
        "article_number": article_number,
        "title": title,
        "clean_text_path": str(clean_path),
        "created_at": now,
    }

    save_index(index_path, index_data)

    print(f"[article_store] saved article: {title}")
    print(f"[article_store] raw chars={len(raw_text)}, clean chars={len(clean_text)}")

    return meta
