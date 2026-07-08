"""Article extraction flow: screenshot -> OCR -> analyze -> scroll."""

from __future__ import annotations

from pathlib import Path
from typing import List

from navigator import scroll_down
from ocr_client import ocr_image
from screen_capture import capture_screenshot
from screen_analyzer import analyze_screen, append_analysis_log
from text_cleaner import merge_text_blocks


MAX_SCROLLS_PER_ARTICLE = 8
SCREENSHOT_DELAY_SECONDS = 1.0
ARTICLE_REGION = (360, 80, 900, 900)


def extract_current_article(article_index: int, work_dir: str | Path, max_scrolls: int = MAX_SCROLLS_PER_ARTICLE) -> str:
    """Extract the currently opened article and return cleaned text."""
    work_path = Path(work_dir)
    screenshot_dir = work_path / "screenshots" / "article_{}".format(article_index)
    analysis_log_path = work_path / "logs" / "article_{}_analysis.jsonl".format(article_index)
    text_blocks: List[str] = []

    for step in range(1, max_scrolls + 1):
        screenshot_path = screenshot_dir / "step_{}.png".format(step)
        capture_screenshot(
            screenshot_path,
            delay_seconds=SCREENSHOT_DELAY_SECONDS,
            region=ARTICLE_REGION,
        )
        text = ocr_image(str(screenshot_path))
        analysis = analyze_screen(
            image_path=str(screenshot_path),
            article_index=article_index,
            step=step,
            ocr_text=text,
            max_scrolls=max_scrolls,
        )
        append_analysis_log(analysis_log_path, analysis, str(screenshot_path), text)

        print(
            "[article {} step {}] page_type={} save={} continue={} bottom={} reason={}".format(
                article_index,
                step,
                analysis.page_type,
                analysis.should_save_text,
                analysis.continue_reading,
                analysis.reached_bottom,
                analysis.reason,
            )
        )

        if analysis.should_save_text and text.strip():
            text_blocks.append(text)

        if not analysis.continue_reading or analysis.reached_bottom:
            break

        if analysis.page_type in ("popup", "error", "article_list", "unknown"):
            print("Unexpected page_type={}, stop current article.".format(analysis.page_type))
            break

        scroll_down(analysis.scroll_clicks)

    return merge_text_blocks(text_blocks)


def save_article_text(article_index: int, text: str, output_dir: str | Path) -> Path:
    article_dir = Path(output_dir) / "articles"
    article_dir.mkdir(parents=True, exist_ok=True)
    output_path = article_dir / "article_{}.txt".format(article_index)
    output_path.write_text(text, encoding="utf-8")
    return output_path
