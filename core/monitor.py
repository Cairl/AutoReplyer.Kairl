"""
Background OCR monitor: scans the latest received bubble for trigger keywords, auto-replies.

Detection logic:
  1. Screenshot the group's message_region.
  2. Locate the bottom-left chat bubble by color:
       night mode #2F2F30 (47,47,48) / day mode #EEEEF0 (238,238,240).
  3. OCR only that bubble (the latest received message).
  4. If a trigger keyword matches AND this message hasn't been replied yet,
     auto-reply once. Per-message hash dedup guarantees exactly one reply.
"""

import time
import threading
from datetime import datetime

import pyautogui

from core.config import Config


class Monitor:
    """
    Background OCR monitor: scans the latest received bubble for trigger keywords, auto-replies.
    Runs in a daemon thread. Shares stats dict with TUI for live display.

    Dedup model: per group, the md5 hash of the last replied bubble text is kept.
    A trigger fires only when the current bottom-left bubble's text hash differs
    from the stored one — guaranteeing each trigger message gets exactly one reply,
    no stale replies, no duplicate replies.
    """

    def __init__(self, config: Config):
        self.config = config
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ocr_engine = None

        # Shared stats (read by TUI)
        self.stats = {
            "running": False,
            "scans": 0,
            "triggers": 0,
            "replies": 0,
            "errors": 0,
            "last_trigger": "N/A",
            "last_reply": "N/A",
            "status": "空闲"
        }
        # group_name -> md5 hex of last replied bubble text
        self._last_replied_hash: dict[str, str] = {}

    def _init_ocr(self):
        """Lazy-init winocr."""
        if self._ocr_engine is None:
            try:
                import winocr
                self._ocr_engine = winocr
            except ImportError:
                raise RuntimeError("winocr 未安装，请运行: pip install winocr")

    def start(self):
        if self.stats["running"]:
            return
        self._init_ocr()
        self._stop_event.clear()
        self.stats["running"] = True
        self.stats["status"] = "启动中..."
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        if not self.stats["running"]:
            return
        self._stop_event.set()
        self.stats["running"] = False
        self.stats["status"] = "正在停止..."
        if self._thread:
            self._thread.join(timeout=5)
        self.stats["status"] = "Stopped"
        self._thread = None

    def _run_loop(self):
        """Main monitoring loop. Runs in daemon thread."""
        self.stats["status"] = "运行中"

        while not self._stop_event.is_set():
            try:
                self.config.hot_reload()
                scan_interval = self.config.get_reply("scan_interval", 2.0)
                groups = self.config.get_groups()

                for i, group in enumerate(groups):
                    if self._stop_event.is_set():
                        break
                    if not group.get("enabled", True):
                        continue
                    if not group.get("message_region"):
                        continue

                    self._scan_group(group)

                self.stats["scans"] += 1

            except Exception as e:
                self.stats["errors"] += 1
                self.stats["status"] = f"错误: {str(e)[:40]}"

            self._stop_event.wait(timeout=scan_interval)

        self.stats["status"] = "Stopped"

    def _find_bottom_left_bubble(self, screenshot):
        """Locate the bottom-most left-side chat bubble by color.

        WeChat received-message bubble colors:
          - Night mode: #2F2F30 = (47, 47, 48)
          - Day mode:   #EEEEF0 = (238, 238, 240)
        Both colors are matched simultaneously; whichever is present is used.
        Only the left half of the region is scanned (received messages are
        left-aligned; sent messages are right-aligned and thus excluded).

        Returns (x, y, w, h) relative to the screenshot, or None if no bubble.
        """
        try:
            import numpy as np
        except ImportError:
            raise RuntimeError("numpy 未安装，请运行: pip install numpy")

        img = np.array(screenshot.convert("RGB"))
        h, w = img.shape[:2]

        night = np.array([47, 47, 48], dtype=np.int16)
        day = np.array([238, 238, 240], dtype=np.int16)
        tol = 8

        diff = img.astype(np.int16)
        mask = (
            np.all(np.abs(diff - night) <= tol, axis=2)
            | np.all(np.abs(diff - day) <= tol, axis=2)
        )

        # Left half only — filters out self-sent (right-side) bubbles.
        half_w = w // 2
        left_mask = mask[:, :half_w]
        row_has = left_mask.any(axis=1)

        rows_with = np.where(row_has)[0]
        if len(rows_with) == 0:
            return None

        bottom = int(rows_with[-1])

        # Walk upward through the continuous band to find the bubble's top.
        top = bottom
        while top > 0 and row_has[top - 1]:
            top -= 1

        # Horizontal extent of this band within the left half.
        band = left_mask[top:bottom + 1, :]
        cols = np.where(band.any(axis=0))[0]
        if len(cols) == 0:
            return None

        x_min = int(cols[0])
        x_max = int(cols[-1])
        bw = x_max - x_min + 1
        bh = bottom - top + 1

        # Reject tiny noise patches.
        if bw < 40 or bh < 15:
            return None

        return (x_min, top, bw, bh)

    def _scan_group(self, group: dict):
        """Scan one group's latest received bubble for trigger keywords.

        Only the bottom-left bubble is OCR'd. A trigger fires at most once per
        unique bubble text (md5 dedup), so the same on-screen message never
        triggers twice and stale messages are never re-replied.
        """
        import hashlib

        region = group["message_region"]
        name = group["name"]

        try:
            screenshot = pyautogui.screenshot(region=(
                region["x"], region["y"], region["w"], region["h"]
            ))

            bubble = self._find_bottom_left_bubble(screenshot)
            if bubble is None:
                return

            bx, by, bw, bh = bubble
            bubble_img = screenshot.crop((bx, by, bx + bw, by + bh))

            result = self._ocr_engine.recognize_pil_sync(bubble_img, "zh-Hans-CN")
            # winocr returns a dict, not an object — use .get() not getattr()
            text = result.get("text", "") if isinstance(result, dict) else getattr(result, "text", "")
            text = (text or "").strip()
            text = " ".join(text.split())  # normalize whitespace for stable hashing

            if not text:
                return

            trigger_str = self.config.get_reply("trigger", "@所有人")
            patterns = [p.strip() for p in trigger_str.split(",") if p.strip()] or ["@所有人"]
            if not any(pat in text for pat in patterns):
                return

            # Dedup: exactly one reply per unique trigger message.
            text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            if text_hash == self._last_replied_hash.get(name):
                return  # already replied to this exact message

            self._last_replied_hash[name] = text_hash
            self.stats["triggers"] += 1
            self.stats["last_trigger"] = f"{name} @ {datetime.now().strftime('%H:%M:%S')}"

            if group.get("reply_region"):
                self._auto_reply(group)

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["status"] = f"扫描错误 ({name}): {type(e).__name__}: {str(e)[:30]}"

    def _auto_reply(self, group: dict):
        """Send auto-reply to a group's reply region."""
        import random

        region = group["reply_region"]
        content = self.config.get_reply("content", "收到")
        delay_min = self.config.get_reply("delay_min", 1.0)
        delay_max = self.config.get_reply("delay_max", 3.0)

        delay = random.uniform(delay_min, delay_max)
        self.stats["status"] = f"等待 {delay:.1f} 秒后回复..."
        time.sleep(delay)

        try:
            click_x = region["x"] + region["w"] // 2
            click_y = region["y"] + region["h"] // 2
            pyautogui.click(click_x, click_y)

            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(content, win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
            except ImportError:
                import subprocess
                proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
                proc.communicate(content.encode("utf-16-le"))

            pyautogui.hotkey("ctrl", "v")
            pyautogui.press("enter")

            self.stats["replies"] += 1
            self.stats["last_reply"] = f"{group['name']} @ {datetime.now().strftime('%H:%M:%S')}"
            self.stats["status"] = f"已回复 {group['name']}"

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["status"] = f"回复错误: {str(e)[:30]}"
