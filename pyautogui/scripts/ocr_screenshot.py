"""Take a PyAutoGUI screenshot and extract visible text with Qwen-VL.

Usage:
  python scripts/ocr_screenshot.py
  python scripts/ocr_screenshot.py --image screenshot0.png

Set MAAS_API_KEY in your environment, or paste it into API_KEY below.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# Fill your API key here if you do not want to use the MAAS_API_KEY env var.
API_KEY = os.getenv("MAAS_API_KEY") or "PASTE_YOUR_MAAS_API_KEY_HERE"

DEFAULT_API_URL = os.getenv("MAAS_API_URL", "https://api.modelarts-maas.com/v1/chat/completions")
DEFAULT_MODEL = os.getenv("QWEN_OCR_MODEL", "qwen2.5-vl-72b")

OCR_PROMPT = """你是一个 OCR 文本提取器。请从截图中提取所有可见文字。

要求：
1. 尽可能完整保留截图中的所有文字。
2. 按照从上到下、从左到右的阅读顺序输出。
3. 不要总结、不要改写、不要判断和补充。
4. 看不清的文字用 [无法识别] 标记。
5. 如果文字属于按钮、标题、菜单、正文、时间、链接等，请尽量保留原样。
6. 输出纯文本。

输出格式：

[OCR_TEXT]
这里放提取到的全部文字
[/OCR_TEXT]
"""


def take_screenshot(output_path: Path) -> Path:
    """Use PyAutoGUI's existing mini-language screenshot command: ss."""
    import pyautogui

    old_cwd = Path.cwd()
    try:
        os.chdir(output_path.parent)
        counter = [0]
        pyautogui.run("ss", _ssCount=counter)
        captured = output_path.parent / "screenshot0.png"
        if captured.resolve() != output_path.resolve():
            if output_path.exists():
                output_path.unlink()
            captured.rename(output_path)
    finally:
        os.chdir(old_cwd)

    if not output_path.exists():
        raise RuntimeError("Screenshot command finished, but no image file was created.")
    return output_path


def image_to_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return "data:{};base64,{}".format(mime_type, encoded)


def call_qwen_ocr(image_path: Path, model: str, api_key: str, api_url: str) -> str:
    if not api_key or api_key == "PASTE_YOUR_MAAS_API_KEY_HERE":
        raise RuntimeError(
            "Missing API key. Set MAAS_API_KEY, or paste your key into API_KEY in scripts/ocr_screenshot.py."
        )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
                ],
            }
        ],
        "temperature": 0.1,
    }

    request = Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("Qwen OCR API HTTP {}: {}".format(exc.code, detail)) from exc
    except URLError as exc:
        raise RuntimeError("Qwen OCR API request failed: {}".format(exc.reason)) from exc

    choices = data.get("choices") or []
    if choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    chunks.append(item["text"])
            if chunks:
                return "\n".join(chunks).strip()

    raise RuntimeError("Could not find text in API response: {}".format(json.dumps(data, ensure_ascii=False)[:1000]))


def ensure_ocr_wrapper(text: str) -> str:
    if "[OCR_TEXT]" in text and "[/OCR_TEXT]" in text:
        return text
    return "[OCR_TEXT]\n{}\n[/OCR_TEXT]".format(text.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screenshot current screen and OCR it with an API.")
    parser.add_argument("--image", type=Path, help="Use an existing image instead of taking a new screenshot.")
    parser.add_argument("--out", type=Path, help="Where to save the new screenshot.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Qwen-VL model to use.")
    parser.add_argument("--api-key", default=API_KEY, help="API key. Prefer MAAS_API_KEY instead of this flag.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="ModelArts MaaS chat completions API URL.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.image:
        image_path = args.image
    else:
        default_name = "ocr_screenshot_{}.png".format(time.strftime("%Y%m%d_%H%M%S"))
        image_path = args.out or Path(default_name)
        image_path = take_screenshot(image_path)

    if not image_path.exists():
        raise FileNotFoundError("Image not found: {}".format(image_path))

    ocr_text = call_qwen_ocr(image_path, args.model, args.api_key, args.api_url)
    print(ensure_ocr_wrapper(ocr_text))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("[OCR_TEXT]\n[无法识别: {}]\n[/OCR_TEXT]".format(exc), file=sys.stderr)
        raise SystemExit(1)
