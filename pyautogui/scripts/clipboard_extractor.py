"""Extract article text with Ctrl+A / Ctrl+C and clipboard."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Iterable

import pyautogui
import pyperclip


# Multiple candidate article-body focus points.
# These points try to avoid cover images, sidebars, and blank right margins.
# Tune them for your own screen if needed.
ARTICLE_FOCUS_POINTS = [
    (1200, 420),
    (1200, 560),
    (1100, 620),
    (1300, 620),
    (1200, 720),
]

COPY_WAIT_SECONDS = 1.0
FOCUS_WAIT_SECONDS = 0.5
MIN_VALID_TEXT_CHARS = 200


def copy_once_at_point(x: int, y: int) -> str:
    """Try copying page text once at a specific focus point."""
    pyperclip.copy("__EMPTY_CLIPBOARD__")

    print(f"[clipboard] focus article body at ({x}, {y})")
    pyautogui.click(x, y)
    time.sleep(FOCUS_WAIT_SECONDS)

    print("[clipboard] Ctrl+A")
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.4)

    print("[clipboard] Ctrl+C")
    pyautogui.hotkey("ctrl", "c")
    time.sleep(COPY_WAIT_SECONDS)

    text = pyperclip.paste()

    if not text or text == "__EMPTY_CLIPBOARD__":
        return ""

    return text.strip()


def extract_text_by_clipboard(
    focus_points: Iterable[tuple[int, int]] = ARTICLE_FOCUS_POINTS,
    min_valid_chars: int = MIN_VALID_TEXT_CHARS,
) -> str:
    """Try multiple focus points and return copied article text."""
    best_text = ""

    for attempt, (x, y) in enumerate(focus_points, start=1):
        print(f"[clipboard] copy attempt {attempt}")

        # If the previous click opened an image preview, try to close it first.
        pyautogui.press("esc")
        time.sleep(0.3)

        text = copy_once_at_point(x, y)
        text_len = len(text)

        print(f"[clipboard] copied chars: {text_len}")

        if text_len > len(best_text):
            best_text = text

        if text_len >= min_valid_chars:
            print("[clipboard] copy looks valid")
            print("[clipboard] preview:")
            print(text[:300])
            return text

        print("[clipboard] copied text too short, try another focus point")

    if best_text:
        print("[clipboard] all attempts failed threshold, return best copied text")
        print(f"[clipboard] best chars: {len(best_text)}")
        print(best_text[:300])
        return best_text

    print("[clipboard] copied text is empty after all attempts")
    return ""


def save_clipboard_article(article_index: int, text: str, output_dir: str | Path = "output") -> Path:
    article_dir = Path(output_dir) / "articles"
    article_dir.mkdir(parents=True, exist_ok=True)

    output_path = article_dir / f"article_{article_index}.txt"
    output_path.write_text(text, encoding="utf-8")

    return output_path
