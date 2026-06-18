"""
Background OCR monitor: scans message regions for @all, auto-replies.
"""

import time
import threading
from datetime import datetime

import pyautogui

from core.config import Config


class Monitor:
    """
    Background OCR monitor: scans message regions for @all, auto-replies.
    Runs in a daemon thread. Shares stats dict with TUI for live display.
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
        self._recent_triggers: dict[str, float] = {}

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

    def _scan_group(self, group: dict):
        """Scan one group's message region for @all trigger."""
        region = group["message_region"]
        name = group["name"]

        try:
            screenshot = pyautogui.screenshot(region=(
                region["x"], region["y"], region["w"], region["h"]
            ))

            result = self._ocr_engine.recognize_pil_sync(screenshot, "zh-Hans-CN")
            all_text = result.get("text", "")

            trigger_str = self.config.get_reply("trigger", "@所有人")
            patterns = [p.strip() for p in trigger_str.split(",") if p.strip()] or ["@所有人"]
            triggered = any(pat in all_text for pat in patterns)

            if triggered:
                now = time.time()
                last = self._recent_triggers.get(name, 0)
                if now - last < 30:
                    return

                self._recent_triggers[name] = now
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
            time.sleep(0.3)

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
            time.sleep(0.2)
            pyautogui.press("enter")

            self.stats["replies"] += 1
            self.stats["last_reply"] = f"{group['name']} @ {datetime.now().strftime('%H:%M:%S')}"
            self.stats["status"] = f"已回复 {group['name']}"

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["status"] = f"回复错误: {str(e)[:30]}"
