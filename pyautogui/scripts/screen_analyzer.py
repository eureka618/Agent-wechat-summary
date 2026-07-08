"""DeepSeek-based screen analysis hook for guiding navigation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEEPSEEK_API_KEY = (
    os.getenv("DEEPSEEK_MAAS_API_KEY")
    or os.getenv("MAAS_API_KEY")
    or "PASTE_YOUR_DEEPSEEK_MAAS_API_KEY_HERE"
)
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_MAAS_API_URL", "https://api.modelarts-maas.com/v2/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_ANALYZER_MODEL", "deepseek-v4-flash")

SYSTEM_PROMPT = """你是一个桌面自动化导航助手。
你正在辅助程序读取微信公众号文章正文。
你只能根据 OCR 文本、当前步骤和滚动上限判断下一步动作。
不要总结文章内容，只判断是否继续阅读、滚动幅度和停止原因。"""

ANALYSIS_PROMPT = """请分析当前公众号页面 OCR 文本，判断当前截图是否属于正在阅读的文章正文页面。

你只负责导航判断，不要总结文章内容。

请返回 JSON，字段如下：
{
  "page_type": "article_detail/article_list/bottom/loading/popup/error/unknown",
  "should_save_text": true,
  "continue_reading": true,
  "scroll_clicks": -6,
  "reached_bottom": false,
  "reason": "简短说明"
}

判断规则：
1. 如果当前 OCR 包含文章标题、公众号名称、发布时间、正文段落，则 page_type 为 article_detail。
2. 如果当前 OCR 是文章正文的一部分，should_save_text=true。
3. 如果当前 OCR 是文章列表、弹窗、加载页、错误页，should_save_text=false。
4. 如果已经接近文章底部，但当前屏仍包含最后一部分正文，should_save_text=true，continue_reading=false。
5. 如果出现“写留言”“阅读原文”“喜欢此内容的人还喜欢”“推荐阅读”等底部特征，可以判断 reached_bottom=true。
6. 如果 OCR 为空或明显异常，should_save_text=false，可以 continue_reading=true 或 false，取决于是否像加载中。
7. scroll_clicks 通常为 -6；如果不需要滚动，则为 0。

只返回 JSON，不要输出额外解释。
"""


@dataclass
class ScreenAnalysis:
    page_type: str = "unknown"
    should_save_text: bool = False
    continue_reading: bool = True
    scroll_clicks: int = -6
    reason: str = "default_continue"
    reached_bottom: bool = False


def _fallback_analyze_screen(step: int, ocr_text: str, max_scrolls: int) -> ScreenAnalysis:
    text = ocr_text.strip()

    if not text:
        return ScreenAnalysis(
            page_type="unknown",
            should_save_text=False,
            continue_reading=True,
            scroll_clicks=-6,
            reason="empty_ocr_continue",
            reached_bottom=False,
        )

    if step >= max_scrolls:
        return ScreenAnalysis(
            page_type="article_detail",
            should_save_text=True,
            continue_reading=False,
            scroll_clicks=0,
            reason="max_scrolls_reached",
            reached_bottom=True,
        )

    bottom_markers = (
        "写留言",
        "阅读原文",
        "喜欢此内容的人还喜欢",
        "推荐阅读",
        "已无更多内容",
    )

    if any(marker in text for marker in bottom_markers):
        return ScreenAnalysis(
            page_type="article_detail",
            should_save_text=True,
            continue_reading=False,
            scroll_clicks=0,
            reason="possible_article_bottom_marker",
            reached_bottom=True,
        )

    return ScreenAnalysis(
        page_type="article_detail",
        should_save_text=True,
        continue_reading=True,
        scroll_clicks=-6,
        reason="fallback_continue",
        reached_bottom=False,
    )


def _extract_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("DeepSeek response did not contain a JSON object: {}".format(text[:500]))
    return json.loads(match.group(0))


def _analysis_from_dict(data: dict) -> ScreenAnalysis:
    scroll_clicks = int(data.get("scroll_clicks", -6))
    if scroll_clicks < -10:
        scroll_clicks = -10
    if scroll_clicks > 3:
        scroll_clicks = 3

    return ScreenAnalysis(
        page_type=str(data.get("page_type", "unknown")),
        should_save_text=bool(data.get("should_save_text", False)),
        continue_reading=bool(data.get("continue_reading", True)),
        scroll_clicks=scroll_clicks,
        reason=str(data.get("reason", "deepseek_analysis")),
        reached_bottom=bool(data.get("reached_bottom", False)),
    )


def _call_deepseek_analyzer(
    image_path: str,
    article_index: int,
    step: int,
    ocr_text: str,
    max_scrolls: int,
    api_key: str,
    api_url: str,
    model: str,
) -> ScreenAnalysis:
    user_content = "\n".join(
        [
            ANALYSIS_PROMPT,
            "article_index: {}".format(article_index),
            "step: {}".format(step),
            "max_scrolls: {}".format(max_scrolls),
            "image_path: {}".format(image_path),
            "OCR_TEXT:",
            ocr_text.strip() or "[空 OCR 文本]",
        ]
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
    }

    request = Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("DeepSeek analyzer API HTTP {}: {}".format(exc.code, detail)) from exc
    except URLError as exc:
        raise RuntimeError("DeepSeek analyzer API request failed: {}".format(exc.reason)) from exc

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek analyzer response has no choices: {}".format(result))

    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", item)) for item in content)
    return _analysis_from_dict(_extract_json_object(str(content)))


def analyze_screen(
    image_path: str,
    article_index: int,
    step: int,
    ocr_text: str,
    max_scrolls: int,
) -> ScreenAnalysis:
    """Analyze the current article screen and decide the next navigation move."""
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "PASTE_YOUR_DEEPSEEK_MAAS_API_KEY_HERE":
        return _fallback_analyze_screen(step, ocr_text, max_scrolls)

    try:
        return _call_deepseek_analyzer(
            image_path=image_path,
            article_index=article_index,
            step=step,
            ocr_text=ocr_text,
            max_scrolls=max_scrolls,
            api_key=DEEPSEEK_API_KEY,
            api_url=DEEPSEEK_API_URL,
            model=DEEPSEEK_MODEL,
        )
    except Exception as exc:
        fallback = _fallback_analyze_screen(step, ocr_text, max_scrolls)
        fallback.reason = "deepseek_failed_then_{}".format(fallback.reason)
        return fallback


def append_analysis_log(log_path: str | Path, analysis: ScreenAnalysis, image_path: str, ocr_text: str) -> None:
    """Append one JSONL analysis record for debugging."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = asdict(analysis)
    record["image_path"] = image_path
    record["ocr_preview"] = ocr_text.strip()[:300]
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
