"""Locate public-account article cards from a full-screen screenshot using a vision model.

This module replaces fixed-coordinate article clicking.

Input:
    A full-screen screenshot of the current WeChat/browser public-account article list.

Output:
    A list of LocatedArticle objects:
    - order
    - title
    - bbox
    - click_point
    - confidence

The click_point should be in full-screen coordinates because the screenshot is full-screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import json
import mimetypes
import os
import re
from typing import List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_KEY = os.getenv("MAAS_API_KEY") or "PASTE_YOUR_MAAS_API_KEY_HERE"
API_URL = os.getenv("MAAS_API_URL", "https://api.modelarts-maas.com/v1/chat/completions")
MODEL = os.getenv("QWEN_LOCATOR_MODEL", "qwen2.5-vl-72b")


LOCATE_PROMPT = """你是一个桌面 GUI 视觉定位器。

现在给你一张电脑全屏截图。截图中应该是微信公众号文章列表页、公众号历史消息页、微信会话里的公众号文章卡片，或者浏览器里的公众号文章列表。

你的任务：
找出屏幕上最上面的 3 篇“可点击文章标题/文章卡片”，并返回它们的点击坐标。

重要要求：
1. 不要点击头像。
2. 不要点击左上角头像。
3. 不要点击左侧侧边栏。
4. 不要点击搜索框。
5. 不要点击菜单栏、返回按钮、公众号头像。
6. 优先选择文章标题文字区域的中心点。
7. 如果标题文字不完整，可以选择文章卡片主体区域的中心点。
8. 坐标必须基于整张截图，左上角为 (0,0)。
9. 如果只能找到 1 或 2 篇文章，就只返回找到的。
10. click_point 必须落在文章标题或文章卡片内部，不要落在头像、边栏、空白处。
11. 不要输出解释，只输出 JSON。

输出格式：

{
  "articles": [
    {
      "order": 1,
      "title": "",
      "bbox": [x1, y1, x2, y2],
      "click_point": [x, y],
      "confidence": 0.0
    }
  ],
  "notes": ""
}
"""


@dataclass
class LocatedArticle:
    order: int
    title: str
    bbox: list[int]
    click_point: tuple[int, int]
    confidence: float


def image_to_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_json(text: str) -> dict:
    """Extract a JSON object from a model response."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {text[:500]}")

    return json.loads(match.group(0))


def call_vlm_locator(image_path: Path) -> dict:
    """Call Qwen-VL to locate article cards."""
    if not API_KEY or API_KEY == "PASTE_YOUR_MAAS_API_KEY_HERE":
        raise RuntimeError("Missing MAAS_API_KEY. Set it in environment variables.")

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": LOCATE_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
                ],
            }
        ],
        "temperature": 0.0,
    }

    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Locator API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Locator API request failed: {exc.reason}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"No choices in locator response: {data}")

    content = choices[0].get("message", {}).get("content", "")

    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                chunks.append(item["text"])
            else:
                chunks.append(str(item))
        content = "\n".join(chunks)

    return extract_json(str(content))


def locate_articles(image_path: str | Path) -> List[LocatedArticle]:
    """Locate clickable article cards from a full-screen screenshot."""
    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(f"List screenshot not found: {image_path}")

    result = call_vlm_locator(image_path)

    articles: List[LocatedArticle] = []

    for item in result.get("articles", []):
        try:
            order = int(item.get("order"))
            title = str(item.get("title", "")).strip()
            bbox = [int(v) for v in item.get("bbox", [])]
            click = item.get("click_point", [])

            if len(click) != 2:
                continue

            x, y = int(click[0]), int(click[1])
            confidence = float(item.get("confidence", 0.0))

            # Basic sanity check: avoid obviously invalid coordinates.
            if x < 0 or y < 0:
                continue

            articles.append(
                LocatedArticle(
                    order=order,
                    title=title,
                    bbox=bbox,
                    click_point=(x, y),
                    confidence=confidence,
                )
            )
        except Exception:
            continue

    articles.sort(key=lambda a: a.order)
    return articles
