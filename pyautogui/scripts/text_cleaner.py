"""Text cleanup utilities for OCR output."""

from __future__ import annotations

from typing import Iterable, List


def normalize_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def merge_text_blocks(text_blocks: Iterable[str], max_overlap_lines: int = 12) -> str:
    """Merge OCR blocks while removing only adjacent overlap."""
    merged: List[str] = []

    for block in text_blocks:
        lines = normalize_lines(block)
        if not lines:
            continue

        if not merged:
            merged.extend(lines)
            continue

        overlap = find_suffix_prefix_overlap(merged, lines, max_overlap_lines=max_overlap_lines)
        merged.extend(lines[overlap:])

    return "\n".join(merged).strip() + ("\n" if merged else "")


def find_suffix_prefix_overlap(existing: List[str], new: List[str], max_overlap_lines: int = 12) -> int:
    max_k = min(len(existing), len(new), max_overlap_lines)

    for k in range(max_k, 0, -1):
        if existing[-k:] == new[:k]:
            return k

    return 0
