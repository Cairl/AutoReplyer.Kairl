"""
Red rectangle overlay: draws a thin red box around a screen region to show
which area the OCR monitor just captured.

Multi-monitor / DPI correctness
-------------------------------
The root cause of red-box misalignment on scaled or multi-monitor setups is a
unit mismatch: pyautogui screenshots and the stored message_region use
**physical pixels**, while a normal tkinter window runs in **logical DIP**
(scaled by the per-monitor DPI scaling factor).

We fix it at the source by marking the process (or this window) as
"DPI-aware / per-monitor-v2" so tkinter itself receives **physical-pixel**
geometry. Then every coordinate the overlay draws with is in the same
physical-pixel space as the region we were handed, and rectangles land exactly
where the screenshot was taken — regardless of monitor count or scale.

The full virtual screen bounding box is queried via the Win32 API
(SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN / SM_CXVIRTUALSCREEN /
SM_CYVIRTUALSCREEN) so the overlay spans every monitor (including negative
offsets for monitors placed to the left/above the primary).

Design
------
  - One borderless, transparent, always-on-top, click-through tkinter window
    sized to the whole virtual screen.
  - Only the requested rectangle (red outline) is ever drawn; the rest is
    fully transparent (color-keyed via `-transparentcolor`).
  - A poller thread (daemon) drives tkinter's mainloop via a queue: the
    monitor pushes a region to show, the poller schedules a reveal + auto-hide.
"""

import ctypes
import queue
import threading
import tkinter as tk


# ── Win32 helpers (best-effort; no-op on non-Windows) ──

def _set_dpi_aware():
    """Make this process DPI-aware so tkinter uses physical-pixel geometry.

    Tries Per-Monitor V2 first (Windows 10 1703+), falls back to the older
    System DPI awareness, then to the legacy SetProcessDPIAware. Safe to call
    multiple times; failures are ignored (overlay just won't be pixel-perfect).
    """
    try:
        # Per-Monitor DPI awareness v2
        PROCESS_PER_MONITOR_DPI_AWARE_V2 = 2
        if hasattr(ctypes.windll.shcore, "SetProcessDpiAwareness"):
            h = ctypes.windll.shcore.SetProcessDpiAwareness(
                PROCESS_PER_MONITOR_DPI_AWARE_V2
            )
            if h == 0:  # S_OK
                return
    except Exception:
        pass
    try:
        if hasattr(ctypes.windll.user32, "SetProcessDPIAware"):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _virtual_screen_rect():
    """Return (x, y, w, h) of the full virtual screen in physical pixels.

    Covers all monitors, including negative x/y for monitors placed left/above
    the primary. Falls back to tkinter's screen size on non-Windows.
    """
    try:
        user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, None, None  # let caller fall back to tkinter metrics


class RegionOverlay:
    """Click-through red rectangle overlay driven by a poller thread."""

    # How long the red box stays visible after a show() call (seconds).
    HOLD_SECONDS = 0.5

    def __init__(self):
        self._queue: "queue.Queue[dict | str]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._rect_id = None
        self._reply_rect_id = None
        self._hide_job = None
        self._vx = 0      # virtual screen origin (physical px)
        self._vy = 0

    # ── lifecycle (called from main thread) ──

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._tk_mainloop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread is None:
            return
        self._queue.put("stop")
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    # ── API (called from monitor thread) ──

    def show(self, region: dict, reply_region: dict | None = None):
        """Briefly flash a red box around the given {x, y, w, h} region,
        and optionally a green box around reply_region."""
        if self._thread is None:
            return
        msg = {"region": dict(region)}
        if reply_region is not None:
            msg["reply_region"] = dict(reply_region)
        self._queue.put(msg)

    # ── internals ──

    def _tk_mainloop(self):
        """Owns the Tk root + mainloop. Runs on a dedicated thread."""
        # Make this process DPI-aware so all geometry below is in physical px.
        _set_dpi_aware()

        try:
            root = tk.Tk()
        except Exception:
            return  # no display / no tkinter — overlay disabled silently
        self._root = root
        self._build_window(root)

        poll = lambda: self._drain_queue()  # noqa: E731
        root.after(50, poll)
        root.mainloop()

        self._root = None
        self._canvas = None
        self._rect_id = None

    def _build_window(self, root: tk.Tk):
        """Full-virtual-screen, transparent, click-through, on-top overlay."""
        root.overrideredirect(True)  # no title bar / border
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", "#010101")  # key color = near-black
        root.config(bg="#010101")
        root.attributes("-disabled", True)  # let clicks pass through

        vx, vy, vw, vh = _virtual_screen_rect()
        if vw is None:
            # Fallback: tkinter screen metrics (single monitor, may be DIP).
            vx, vy = root.winfo_vrootx(), root.winfo_vrooty()
            vw, vh = root.winfo_screenwidth(), root.winfo_screenheight()
        self._vx, self._vy = vx, vy
        # Position the window at the virtual-screen origin and size it to span
        # every monitor. tkinter geometry uses physical px once DPI-aware.
        root.geometry(f"{vw}x{vh}+{vx}+{vy}")

        canvas = tk.Canvas(
            root, bg="#010101", highlightthickness=0,
            width=vw, height=vh, bd=0,
        )
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas

    def _drain_queue(self):
        """Process pending show/stop requests, then reschedule."""
        if self._root is None:
            return
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg == "stop":
                    try:
                        self._root.quit()
                    except Exception:
                        pass
                    return
                self._draw(msg["region"])
        except queue.Empty:
            pass
        self._root.after(50, self._drain_queue)

    def _draw(self, region: dict):
        """Draw red box (received) + optional green box (reply)."""
        if self._canvas is None or self._root is None:
            return

        # --- Received bubble (red #F38B8C, width=3) ---
        x = int(region.get("x", 0)) - self._vx
        y = int(region.get("y", 0)) - self._vy
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        if w > 0 and h > 0:
            coords = (x, y, x + w, y + h)
            if self._rect_id is None:
                self._rect_id = self._canvas.create_rectangle(
                    *coords, outline="#F38B8C", width=3
                )
            else:
                self._canvas.coords(self._rect_id, *coords)
            self._canvas.itemconfig(self._rect_id, state="normal")
            self._canvas.tag_raise(self._rect_id)

        # --- Reply content (green #A6E3A1, width=2) ---
        reply = region.get("reply_region")
        if reply is not None:
            rx = int(reply.get("x", 0)) - self._vx
            ry = int(reply.get("y", 0)) - self._vy
            rw = int(reply.get("w", 0))
            rh = int(reply.get("h", 0))
            if rw > 0 and rh > 0:
                rcoords = (rx, ry, rx + rw, ry + rh)
                if self._reply_rect_id is None:
                    self._reply_rect_id = self._canvas.create_rectangle(
                        *rcoords, outline="#A6E3A1", width=2
                    )
                else:
                    self._canvas.coords(self._reply_rect_id, *rcoords)
                self._canvas.itemconfig(self._reply_rect_id, state="normal")
                self._canvas.tag_raise(self._reply_rect_id)
        else:
            if self._reply_rect_id is not None:
                self._canvas.itemconfig(self._reply_rect_id, state="hidden")

        # Auto-hide
        if self._hide_job is not None:
            try:
                self._root.after_cancel(self._hide_job)
            except Exception:
                pass
        self._hide_job = self._root.after(
            int(self.HOLD_SECONDS * 1000), self._hide
        )

    def _hide(self):
        if self._canvas is not None:
            if self._rect_id is not None:
                self._canvas.itemconfig(self._rect_id, state="hidden")
            if self._reply_rect_id is not None:
                self._canvas.itemconfig(self._reply_rect_id, state="hidden")
        self._hide_job = None
