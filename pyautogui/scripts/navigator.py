"""Navigation actions for public-account article reader."""

from __future__ import annotations

import time
import pyautogui


CLICK_SLEEP_SECONDS = 3.0
BACK_SLEEP_SECONDS = 2.0


def click_point(x: int, y: int, label: str = "") -> None:
    print(f"[navigator] move to {label}: ({x}, {y})")
    pyautogui.moveTo(x, y, duration=0.7)
    time.sleep(0.25)

    print(f"[navigator] click {label}: ({x}, {y})")
    pyautogui.click(x, y)
    time.sleep(CLICK_SLEEP_SECONDS)


def back_to_article_list() -> None:
    print("[navigator] back to article list by Alt+Left")
    pyautogui.hotkey("alt", "left")
    time.sleep(BACK_SLEEP_SECONDS)
