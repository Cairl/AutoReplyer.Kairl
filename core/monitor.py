"""
Background OCR monitor: scans the latest received bubble for trigger keywords, auto-replies.

Detection logic:
  1. Screenshot the group's message_region.
  2. Locate the bottom-most received bubble on the left half by color:
       night mode #2F2F30 (47,47,48) / day mode #EEEEF0 (238,238,240).
  3. Check the right half BELOW that bubble: high pixel std deviation means a
     reply bubble already sits there (already answered → skip); low variance
     means no reply yet → trigger.
  4. OCR only the latest received bubble.
  5. A trigger fires ONLY when that bubble contains a keyword AND the
     below-area is blank (i.e. we haven't already replied to it).
     A short-lived in-flight guard covers the delay window between detecting a
     trigger and the reply bubble actually appearing on screen.
"""

import time
import threading
import hashlib
from datetime import datetime

import pyautogui

from core.config import Config
from core.overlay import RegionOverlay
from core.gpu import xp

# Pillow always returns CPU numpy arrays; keep plain numpy for the Pillow→array step.
import numpy


class Monitor:
    """
    Background OCR monitor: scans the latest received bubble for trigger keywords, auto-replies.
    Runs in a daemon thread. Shares stats dict with TUI for live display.

    Reply-state model: whether we've already answered is read from the screen,
    not from memory. After OCR-ing the latest received bubble and finding a
    keyword, we check the right half BELOW that bubble for pixel variance
    (std deviation). If the below-area has high variance (>30), a reply bubble
    sits there → already answered → skip. If the below-area is uniform
    (low variance), no reply exists → trigger. A per-group in-flight guard
    (keyed by the bubble's signature) prevents re-firing during the configured
    reply delay, before the reply bubble has had time to render.
    """

    # Received-message bubble colors.
    _NIGHT_RECV = (47, 47, 48)      # #2F2F30
    _DAY_RECV = (238, 238, 240)     # #EEEEF0
    # Self-sent reply bubble colors.
    _LIGHT_REPLY = (157, 242, 159)  # #9DF29F
    _DARK_REPLY = (53, 210, 141)    # #35D28D
    # Min target-color fill ratio inside a candidate box for it to count as a
    # chat bubble: a bubble is a solid rounded rect of text background, so the
    # color must cover most of its bounding box (text occupies only a small
    # fraction). Scattered dots, thin lines and icons fall well below this.
    _BUBBLE_FILL_RATIO = 0.5

    def __init__(self, config: Config):
        self.config = config
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ocr_engine = None
        self._overlay = RegionOverlay()

        # Shared stats (read by TUI)
        self.stats = {
            "running": False,
            "scans": 0,
            "triggers": 0,
            "replies": 0,
            "errors": 0,
            "last_trigger": "N/A",
            "reply_elapsed": None,
            "status": "空闲"
        }
        # group_name -> {signature: expiry_timestamp}: in-flight trigger guard.
        # Keeps a trigger from firing again before its green reply bubble renders.
        self._inflight: dict[str, dict[str, float]] = {}


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
        self._overlay.start()
        self._stop_event.clear()
        self.stats["running"] = True
        self.stats["status"] = "启动中"
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
        self._overlay.stop()
        self.stats["status"] = "Stopped"
        self._thread = None

    def _run_loop(self):
        """Main monitoring loop. Runs in daemon thread."""
        self.stats["status"] = "启动中"
        time.sleep(3)
        self.stats["status"] = "运行中"

        while not self._stop_event.is_set():
            try:
                self.config.hot_reload()
                scan_interval = self.config.get_reply("scan_interval", 2.0)
                groups = self.config.get_groups()

                # Global monitoring master switch (toggled from the top of the
                # TUI; persisted in config.json). When off, no scanning happens.
                if not self.config.get_monitoring():
                    self.stats["status"] = "已暂停"
                    self.stats["scans"] += 1
                    continue  # bottom-of-loop wait handles the scan interval

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

    def _analyze_region(self, screenshot):
        """Locate the latest received bubble and check if a reply pair exists below it.

        Strategy:
          1. Find the bottom-most received bubble on the left half (by color #2F2F30/#EEEEF0).
          2. Look at the right half of the image BELOW that bubble.
          3. If the right-half below-area has high pixel variance → a reply bubble sits there
             → the message has already been answered → skip.
          4. If the below-area is uniform (low variance) → no reply → trigger.

        Returns a dict:
          {"bubble": (x,y,w,h)|None, "bubble_bottom": int|None, "already_replied": bool}
        """
        # Pillow.screenshot → CPU numpy array.
        img_cpu = numpy.array(screenshot.convert("RGB"))
        h, w = img_cpu.shape[:2]
        tol = 8

        # ── GPU-accelerated color matching (heavy pure-array ops) ──
        img_gpu = xp.asarray(img_cpu)
        diff = img_gpu.astype(xp.int16)
        night = xp.array(self._NIGHT_RECV, dtype=xp.int16)
        day = xp.array(self._DAY_RECV, dtype=xp.int16)
        recv_mask_gpu = (
            xp.all(xp.abs(diff - night) <= tol, axis=2)
            | xp.all(xp.abs(diff - day) <= tol, axis=2)
        )
        # Transfer mask back to CPU for control-flow-heavy bubble-finding.
        recv_mask = xp.asnumpy(recv_mask_gpu) if hasattr(xp, "asnumpy") else recv_mask_gpu

        # ── Bubble detection on CPU (numpy, proven control-flow safety) ──
        bubble = self._find_lowest_bubble(recv_mask[:, :w // 2], min_w=40, min_h=15)
        bubble_bottom = bubble[1] + bubble[3] - 1 if bubble else None

        # ── Pair detection: is there content in the right half below the received bubble? ──
        already_replied = False
        reply_box = None
        if bubble is not None and bubble_bottom is not None and bubble_bottom < h - 5:
            below_right_gpu = img_gpu[bubble_bottom:, w // 2:]
            if below_right_gpu.size > 0:
                # Run std on GPU, transfer scalar result to CPU.
                below_std = float(xp.std(below_right_gpu))
                already_replied = below_std > 30
                if already_replied:
                    row_std_gpu = xp.std(below_right_gpu, axis=(1, 2))
                    row_std = xp.asnumpy(row_std_gpu) if hasattr(xp, "asnumpy") else row_std_gpu
                    content_rows = numpy.where(row_std > 20)[0]
                    if len(content_rows) > 0:
                        r_top = int(content_rows[0]) + bubble_bottom
                        r_bottom = int(content_rows[-1]) + bubble_bottom
                        reply_box = (w // 2, r_top, w - w // 2, r_bottom - r_top + 1)
            else:
                already_replied = False

        return {
            "bubble": bubble,
            "bubble_bottom": bubble_bottom,
            "already_replied": already_replied,
            "reply_box": reply_box,
        }

    def _find_lowest_bubble(self, mask, min_w: int, min_h: int):
        """Return the bounding box of the lowest valid chat bubble in `mask`.

        Scans row-bands from the bottom up. For each contiguous band of target
        rows, builds the band's horizontal bounding box and accepts it only if:
          - the box meets min_w × min_h, AND
          - the target color covers >= _BUBBLE_FILL_RATIO of the box area
            (a chat bubble's text-background color fills most of its box;
            thin lines / icons / scattered noise do not).

        The first band from the bottom that passes is returned — so a thin line
        of noise sitting below the real bubble is skipped in favor of the bubble
        above it. Returns (x, y, w, h) or None.

        NOTE: mask is always a CPU numpy array — GPU→CPU transfer happens
        in _analyze_region() before this function is called.
        """
        h, w = mask.shape[:2]
        row_has = mask.any(axis=1)

        bottom = h - 1
        while bottom >= 0:
            if not row_has[bottom]:
                bottom -= 1
                continue
            # This row has target pixels → find the band it belongs to.
            band_bottom = bottom
            band_top = band_bottom
            while band_top > 0 and row_has[band_top - 1]:
                band_top -= 1

            # Horizontal extent across the whole band.
            band = mask[band_top:band_bottom + 1, :]
            cols = numpy.where(band.any(axis=0))[0]
            if len(cols) > 0:
                x_min = int(cols[0])
                x_max = int(cols[-1])
                bw = x_max - x_min + 1
                bh = band_bottom - band_top + 1
                if bw >= min_w and bh >= min_h:
                    fill = float(mask[band_top:band_bottom + 1, x_min:x_max + 1].mean())
                    if fill >= self._BUBBLE_FILL_RATIO:
                        return (x_min, band_top, bw, bh)

            # Band didn't qualify → continue searching above it.
            bottom = band_top - 1

        return None

    def _scan_group(self, group: dict):
        """Scan one group's latest received bubble for trigger keywords.

        A trigger fires ONLY when ALL hold:
          - the latest bottom-left bubble is found (a received message exists);
          - its OCR text contains a trigger keyword;
          - there is NO reply content at or below that bubble on the right half —
            i.e. we haven't already answered it;
          - the message isn't currently in-flight (a reply is queued/rendering).

        Reading "have I replied?" from the screen (rather than from a memory of
        past texts) avoids re-replying to stale on-screen messages and correctly
        re-replies when the same keyword is sent again after we've answered.
        """
        region = group["message_region"]
        name = group["name"]

        try:
            screenshot = pyautogui.screenshot(region=(
                region["x"], region["y"], region["w"], region["h"]
            ))

            view = self._analyze_region(screenshot)
            bubble = view["bubble"]
            if bubble is None:
                return

            bx, by, bw, bh = bubble

            # Flash boxes: red around received bubble, green around reply content (if any).
            abs_x = region["x"] + bx
            abs_y = region["y"] + by
            reply_region = None
            rbox = view.get("reply_box")
            if rbox:
                rbx, rby, rbw, rbh = rbox
                reply_region = {"x": region["x"] + rbx, "y": region["y"] + rby, "w": rbw, "h": rbh}
            self._overlay.show({"x": abs_x, "y": abs_y, "w": bw, "h": bh}, reply_region)

            bubble_img = screenshot.crop((bx, by, bx + bw, by + bh))

            result = self._ocr_engine.recognize_pil_sync(bubble_img, "zh-Hans-CN")
            # winocr returns a dict, not an object — use .get() not getattr()
            text = result.get("text", "") if isinstance(result, dict) else getattr(result, "text", "")
            text = (text or "").strip()
            text = " ".join(text.split())

            if not text:
                return

            trigger_str = self.config.get_reply("trigger", "@所有人")
            patterns = [p.strip() for p in trigger_str.split(",") if p.strip()] or ["@所有人"]
            if not any(pat in text for pat in patterns):
                return

            # ── Already-replied check (first pass): skip immediately if pair is visible. ──
            if view.get("already_replied"):
                sig = self._bubble_signature(bubble, text)
                self._inflight.get(name, {}).pop(sig, None)
                return

            # Click the message bubble to focus the chat window and expose any hidden
            # reply content or input area that may be covering the pair-detection region.
            abs_center_x = region["x"] + bx + bw // 2
            abs_center_y = region["y"] + by + bh // 2
            pyautogui.click(abs_center_x, abs_center_y)

            # Re-screenshot and re-analyze: the click may have revealed a previously
            # obscured reply bubble, which would change the already_replied result.
            screenshot2 = pyautogui.screenshot(region=(
                region["x"], region["y"], region["w"], region["h"]
            ))
            view = self._analyze_region(screenshot2)
            new_bubble = view["bubble"]
            if new_bubble is None:
                return
            # Update bubble coords in case the view shifted slightly after click.
            bx, by, bw, bh = new_bubble

            # Update overlay with new bubble position + reply content.
            abs_x = region["x"] + bx
            abs_y = region["y"] + by
            reply_region2 = None
            rbox2 = view.get("reply_box")
            if rbox2:
                rbx, rby, rbw, rbh = rbox2
                reply_region2 = {"x": region["x"] + rbx, "y": region["y"] + rby, "w": rbw, "h": rbh}
            self._overlay.show({"x": abs_x, "y": abs_y, "w": bw, "h": bh}, reply_region2)

            # ── Already-replied check (second pass): re-check after exposing hidden content. ──
            if view.get("already_replied"):
                sig = self._bubble_signature(bubble, text)
                self._inflight.get(name, {}).pop(sig, None)
                return

            # ── In-flight guard: a reply for this exact message is already pending. ──
            sig = self._bubble_signature(bubble, text)
            now = time.time()
            inflight = self._inflight.setdefault(name, {})

            # Clean expired entries from inflight.
            inflight.pop(next((k for k, exp in list(inflight.items()) if exp <= now), None), None)

            if sig in inflight:
                return  # reply already queued/rendering for this message

            # Mark in-flight; auto-clear after a generous window covering the reply
            # delay plus time for the green bubble to render on screen.
            delay_max = float(self.config.get_reply("delay_max", 3.0))
            inflight[sig] = now + delay_max + 5.0

            self.stats["_trigger_time"] = time.time()
            self.stats["triggers"] += 1
            ts = datetime.now()
            self.stats["last_trigger"] = f"{name} @ {ts.strftime('%H:%M:%S')}.{ts.strftime('%f')[:3]}"

            if group.get("reply_region"):
                self._auto_reply(group)

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["status"] = f"扫描错误 ({name}): {type(e).__name__}: {str(e)[:30]}"

    @staticmethod
    def _bubble_signature(bubble, text):
        """Stable per-message signature for the in-flight guard.

        Binds the trigger bubble's on-screen position (so a new, later message
        with the same text still fires) to its OCR text (so the same bubble
        moving slightly between scans is treated as one)."""
        bx, by, bw, bh = bubble
        row_bucket = by // 4  # tolerate small vertical jitter
        return hashlib.md5(f"{row_bucket}|{bw}|{bh}|{text}".encode("utf-8")).hexdigest()

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
            self.stats["reply_elapsed"] = time.time() - self.stats.get("_trigger_time", time.time())
            self.stats["status"] = f"已回复 {group['name']}"

            # Send Windows notification
            self._notify(group["name"], content)

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["status"] = f"回复错误: {str(e)[:30]}"

    @staticmethod
    def _notify(group_name: str, content: str):
        """Show a Windows toast notification after replying."""
        try:
            import os, sys

            # winotify requires a Start Menu shortcut with matching app_id.
            # Create one on first use if it doesn't exist.
            appdata = os.environ.get("APPDATA", "")
            lnk_dir = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\AutoReplyer")
            lnk_path = os.path.join(lnk_dir, "AutoReplyer.Kairl.lnk")
            if not os.path.exists(lnk_path):
                os.makedirs(lnk_dir, exist_ok=True)
                from win32com.client import Dispatch
                shell = Dispatch("WScript.Shell")
                shortcut = shell.CreateShortcut(lnk_path)
                shortcut.TargetPath = sys.executable
                shortcut.Save()

            from winotify import Notification
            Notification(
                app_id="AutoReplyer.Kairl",
                title=f"已回复 {group_name}",
                msg=content,
            ).show()
        except Exception:
            pass  # notification is best-effort
