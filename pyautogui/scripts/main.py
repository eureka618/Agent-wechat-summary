"""MVP entry point: locate articles, click, copy text with clipboard, save txt."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1] if Path(__file__).parent.name == "scripts" else Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from article_locator import locate_articles
from article_store import save_article_with_dedup
from backend_client import post_clean_article
from clipboard_extractor import extract_text_by_clipboard
from navigator import back_to_article_list, click_point
from screen_capture import capture_screenshot


DEFAULT_ARTICLE_COUNT = 3
DEFAULT_OUTPUT_DIR = Path("output")
START_DELAY_SECONDS = 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Locate public-account articles and copy text with clipboard.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-delay", type=float, default=START_DELAY_SECONDS)
    parser.add_argument("--article-count", type=int, default=DEFAULT_ARTICLE_COUNT)
    return parser.parse_args()


def capture_and_locate_list(output_dir: Path, label: str):
    list_screenshot = output_dir / "screenshots" / f"{label}.png"

    # Full-screen screenshot keeps VLM coordinates aligned with pyautogui.
    capture_screenshot(list_screenshot, delay_seconds=1.0)

    print(f"[main] locating articles from {list_screenshot} ...")
    articles = locate_articles(list_screenshot)

    if not articles:
        print(f"[error] 没有识别到可点击文章。请检查截图：{list_screenshot}")
        return []

    print("[main] located articles:")
    for article in articles:
        print(
            f"  order={article.order}, title={article.title}, "
            f"bbox={article.bbox}, click_point={article.click_point}, confidence={article.confidence}"
        )

    return articles


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("请在倒计时结束前切换到公众号文章列表页。")
    print(f"{args.start_delay} 秒后开始。")
    time.sleep(args.start_delay)

    processed_count = 0

    while processed_count < args.article_count:
        list_label = "article_list" if processed_count == 0 else f"article_list_after_{processed_count}"
        articles = capture_and_locate_list(args.output_dir, list_label)

        if not articles:
            return 1

        target_index = processed_count

        if target_index >= len(articles):
            print(f"[warning] 只定位到 {len(articles)} 篇文章，无法处理第 {processed_count + 1} 篇。停止。")
            break

        article = articles[target_index]
        article_number = processed_count + 1
        x, y = article.click_point

        print(f"\n=== 开始处理第 {article_number} 篇：{article.title} ===")

        click_point(x, y, label=f"article_{article_number}")

        text = extract_text_by_clipboard()

        if not text.strip():
            print(f"[error] 第 {article_number} 篇复制到的文本为空。")
            print("[hint] 可能原因：没有进入正文页、页面不支持 Ctrl+A/Ctrl+C、焦点点错了。")
            break

        meta = save_article_with_dedup(
            article_number=article_number,
            raw_text=text,
            output_dir=args.output_dir,
            source_title=article.title,
        )

        if meta.get("duplicate"):
            print(f"[main] duplicate article skipped: {meta.get('title')}")
        else:
            print(f"[main] saved article {article_number} -> {meta.get('clean_text_path')}")
            clean_text_path = meta.get("clean_text_path")
            if clean_text_path:
                clean_text = Path(clean_text_path).read_text(encoding="utf-8")
                try:
                    result = post_clean_article(
                        title=meta.get("title", ""),
                        content=clean_text,
                        source="公众号自动采集",
                        metadata=meta,
                    )
                    print(f"[backend] ingest result: {result}")
                except RuntimeError as exc:
                    print(f"[backend] ingest failed: {exc}")

        processed_count += 1

        if processed_count >= args.article_count:
            break

        back_to_article_list()
        time.sleep(1.5)

    print(f"[main] finished. processed_count={processed_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
