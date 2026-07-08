"""OCR client for screenshot image -> plain text."""

from __future__ import annotations

from pathlib import Path
import re

try:
    from scripts.ocr_screenshot import API_KEY, DEFAULT_API_URL, DEFAULT_MODEL, call_qwen_ocr
except ModuleNotFoundError:
    from ocr_screenshot import API_KEY, DEFAULT_API_URL, DEFAULT_MODEL, call_qwen_ocr


def strip_ocr_wrapper(text: str) -> str:
    """Remove [OCR_TEXT] wrapper if the model returns it."""
    if not text:
        return ""

    match = re.search(r"\[OCR_TEXT\](.*?)\[/OCR_TEXT\]", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()

    return text.strip()


def ocr_image(image_path: str) -> str:
    """Extract visible text from a screenshot image."""
    raw_text = call_qwen_ocr(
        image_path=Path(image_path),
        model=DEFAULT_MODEL,
        api_key=API_KEY,
        api_url=DEFAULT_API_URL,
    )
    return strip_ocr_wrapper(raw_text)
