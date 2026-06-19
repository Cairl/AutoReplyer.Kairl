"""
Background OCR monitor: scans the latest received bubble for trigger keywords, auto-replies.

Detection logic:
  1. Screenshot the group's message_region.
  2. Locate received bubbles on the left half by color:
       night mode #2F2F30 (47,47,48) / day mode #EEEEF0 (238,238,240).
  3. Locate self-sent reply bubbles on the right half by color:
       light mode #9DF29F (157,242,159) / dark mode #35D28D (53,210,141).
  4. Pair each received bubble with the nearest green reply bubble that sits
     at/after it and before the next received bubble. This builds explicit
     received->reply pairs and is robust to interleaved messages: a reply
     nestled between two received messages pairs with the one above it.
  5. OCR only the lowest received bubble.
  6. A trigger fires ONLY when that bubble contains a keyword AND it has no
     paired green reply (i.e. we haven't already answered it).
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
    not from memory. We color-match both the received-bubble color (left half,
    #2F2F30/#EEEEF0) and the self-sent reply color (right half,
    #9DF29F/#35D28D), then greedily pair each received bubble with the nearest
    green reply below it (and above the next received bubble). The lowest
    received bubble is "already replied" iff it has a paired green reply.
    Looking for the actual reply color — instead of generic pixel variance on
    the right half — survives interleaved messages and avoids mistaking
    unrelated on-screen content for a reply. A per-group in-flight guard
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
        """Locate the lowest received bubble and check if a paired green reply exists.

        Strategy:
          1. Color-match received bubbles (#2F2F30/#EEEEF0) on the left half.
          2. Color-match self-sent green replies (#9DF29F/#35D28D) on the right half.
          3. Greedily pair each received bubble (top-to-bottom) with the nearest
             unused green reply that sits at/after it and before the next received
             bubble. This correctly handles interleaved messages: a reply nestled
             between two received messages pairs with the one above it, so an
             already-answered @all never re-triggers just because other messages
             landed in between.
          4. The lowest received bubble is "already replied" iff it has a pair.

        Returns a dict:
          {"bubble": (x,y,w,h)|None, "bubble_bottom": int|None,
           "already_replied": bool, "reply_box": (x,y,w,h)|None}
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
        light_reply = xp.array(self._LIGHT_REPLY, dtype=xp.int16)
        dark_reply = xp.array(self._DARK_REPLY, dtype=xp.int16)
        reply_mask_gpu = (
            xp.all(xp.abs(diff - light_reply) <= tol, axis=2)
            | xp.all(xp.abs(diff - dark_reply) <= tol, axis=2)
        )
        # Transfer masks back to CPU for control-flow-heavy bubble-finding.
        recv_mask = xp.asnumpy(recv_mask_gpu) if hasattr(xp, "asnumpy") else recv_mask_gpu
        reply_mask = xp.asnumpy(reply_mask_gpu) if hasattr(xp, "asnumpy") else reply_mask_gpu

        # ── Bubble detection on CPU (numpy, proven control-flow safety) ──
        # All received bubbles on the left half, sorted top-to-bottom by y.
        recv_bubbles = self._find_all_bubbles(recv_mask[:, :w // 2], min_w=40, min_h=15)
        # All green reply bubbles on the right half; shift x back to full-image
        # coordinates so the returned boxes are usable directly.
        reply_raw = self._find_all_bubbles(reply_mask[:, w // 2:], min_w=40, min_h=15)
        reply_bubbles = [(x + w // 2, y, bw, bh) for (x, y, bw, bh) in reply_raw]

        # ── Greedy received→reply pairing ──
        # For each received bubble (top-to-bottom), claim the nearest unused
        # green reply whose top is at/after the received bubble's top and before
        # the next received bubble's top. "At/after" (with a tiny tolerance)
        # covers the case where a reply sits to the right of and roughly level
        # with the received bubble; "before the next received bubble" ensures a
        # reply is never stolen from the message below it.
        reply_used: set[int] = set()
        pairs: dict[int, tuple] = {}
        for i, rb in enumerate(recv_bubbles):
            next_top = recv_bubbles[i + 1][1] if i + 1 < len(recv_bubbles) else h
            best, best_j = None, -1
            for j, rp in enumerate(reply_bubbles):
                if j in reply_used:
                    continue
                if rp[1] < rb[1] - 2:
                    continue  # reply is above this received bubble → belongs to an earlier one
                if rp[1] >= next_top:
                    break  # reply_bubbles is sorted by y; rest belong to the next received bubble
                if best is None or rp[1] < best[1]:
                    best, best_j = rp, j
            if best is not None:
                pairs[i] = best
                reply_used.add(best_j)

        # The trigger candidate is the LOWEST received bubble.
        bubble = recv_bubbles[-1] if recv_bubbles else None
        bubble_bottom = bubble[1] + bubble[3] - 1 if bubble else None
        lowest_idx = len(recv_bubbles) - 1 if recv_bubbles else -1
        already_replied = lowest_idx in pairs
        reply_box = pairs.get(lowest_idx)

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

    def _find_all_bubbles(self, mask, min_w: int, min_h: int):
        """Return all valid bubble bounding boxes in `mask`, sorted top-to-bottom.

        Uses the same acceptance criteria as _find_lowest_bubble (min size +
        target-color fill ratio) but returns every qualifying band instead of
        just the lowest one. Needed for received→reply pairing, which must know
        about every received bubble on screen (not only the lowest) so a reply
        nestled between two received messages is attributed to the right one.

        NOTE: mask is always a CPU numpy array — GPU→CPU transfer happens
        in _analyze_region() before this function is called.
        """
        h, w = mask.shape[:2]
        row_has = mask.any(axis=1)

        bubbles = []
        i = 0
        while i < h:
            if not row_has[i]:
                i += 1
                continue
            # Contiguous band of target-color rows.
            band_top = i
            while i < h and row_has[i]:
                i += 1
            band_bottom = i - 1

            band = mask[band_top:band_bottom + 1, :]
            cols = numpy.where(band.any(axis=0))[0]
            if len(cols) == 0:
                continue
            x_min = int(cols[0])
            x_max = int(cols[-1])
            bw = x_max - x_min + 1
            bh = band_bottom - band_top + 1
            if bw >= min_w and bh >= min_h:
                fill = float(mask[band_top:band_bottom + 1, x_min:x_max + 1].mean())
                if fill >= self._BUBBLE_FILL_RATIO:
                    bubbles.append((x_min, band_top, bw, bh))

        return bubbles

    def _overlay_view(self, region, view):
        """Build an overlay.show() call from a region + analysis view.

        Merges received bubble + paired green reply into one cyan whole-context
        box when paired; otherwise a red box around just the received bubble.
        Returns (region_dict, reply_region_dict|None, paired_bool).
        """
        bubble = view.get("bubble")
        if bubble is None:
            return None, None, False
        bx, by, bw, bh = bubble
        reply_region = None
        rbox = view.get("reply_box")
        if rbox:
            rbx, rby, rbw, rbh = rbox
            reply_region = {
                "x": region["x"] + rbx, "y": region["y"] + rby,
                "w": rbw, "h": rbh,
            }
        return (
            {"x": region["x"] + bx, "y": region["y"] + by, "w": bw, "h": bh},
            reply_region,
            view.get("already_replied", False),
        )

    def _scan_group(self, group: dict):
        """Scan one group's latest received bubble for trigger keywords.

        A trigger fires ONLY when ALL hold:
          - the lowest bottom-left received bubble is found;
          - its OCR text contains a trigger keyword;
          - that bubble has NO paired green reply bubble on the right half —
            i.e. we haven't already answered it;
          - the message isn't currently in-flight (a reply is queued/rendering).

        Visual feedback is shown ONLY for trigger-relevant messages: a non-trigger
        received bubble (e.g. someone's chatter + your unrelated reply) never gets
        outlined, so it isn't mistaken for an active auto-reply context.

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
                return  # non-trigger message: no overlay, no action

            # ── Trigger-relevant from here on: show visual feedback. ──
            # First pass: if already paired with a green reply, flash the merged
            # cyan whole-context box and skip — we've already answered this one.
            rgn, rreply, paired = self._overlay_view(region, view)
            self._overlay.show(rgn, rreply, paired=paired)
            if view.get("already_replied"):
                sig = self._bubble_signature(bubble, text)
                self._inflight.get(name, {}).pop(sig, None)
                return

            # Click the message bubble to focus the chat window and expose any hidden
            # reply content or input area that may be covering the pair-detection region.
            # Use MIDDLE click so we focus the window without selecting text — left-click
            # selects the message text, changing the bubble's background colour and
            # potentially throwing off the colour-based pair detection.
            abs_center_x = region["x"] + bx + bw // 2
            abs_center_y = region["y"] + by + bh // 2
            pyautogui.click(abs_center_x, abs_center_y, button="middle")

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

            # Second-pass overlay with the post-click bubble position.
            rgn2, rreply2, paired2 = self._overlay_view(region, view)
            self._overlay.show(rgn2, rreply2, paired=paired2)

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
