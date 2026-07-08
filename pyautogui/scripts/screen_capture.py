"""Screen capture helpers for the public-account article reader MVP."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Optional, Tuple

import pyautogui


Region = Tuple[int, int, int, int]


def capture_screenshot(image_path: str | Path, delay_seconds: float = 0.8, region: Optional[Region] = None) -> Path:
    """Wait briefly, capture the current screen, and save it as an image."""
    output_path = Path(image_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    time.sleep(delay_seconds)
    image = pyautogui.screenshot(region=region)
    image.save(output_path)
    return output_path
