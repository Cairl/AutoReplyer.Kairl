"""
AutoReplyer.Kairl
WeChat Group @all Auto-Reply Tool
Console TUI with OCR-based screen monitoring
"""

import os
import sys
import json
import time
import math
import copy
import threading
import unicodedata
import tempfile
import ctypes
from pathlib import Path
from datetime import datetime

os.system("")  # Enable VT100 on Windows


# ── Imports (fail fast with clear error if missing) ──
import msvcrt
import pyautogui
from PIL import Image

# ═══════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent / "config.json"
ESC = chr(27)

# ── Catppuccin Mocha color palette ──
class C:
    RESET       = f"{ESC}[0m"
    BOLD        = f"{ESC}[97;1m"        # bright white + bold (standalone \033[1m is invisible)
    DIM         = f"{ESC}[2m"
    RED         = f"{ESC}[38;2;243;139;168m"
    GREEN       = f"{ESC}[38;2;166;227;161m"
    YELLOW      = f"{ESC}[38;2;249;226;175m"
    BLUE        = f"{ESC}[38;2;137;180;250m"
    MAUVE       = f"{ESC}[38;2;203;166;247m"
    TEAL        = f"{ESC}[38;2;148;226;213m"
    GRAY        = f"{ESC}[38;2;148;152;178m"  # #9498B2 — lighter, more readable
    SUBTEXT     = f"{ESC}[38;2;186;194;222m"
    WHITE       = f"{ESC}[38;2;205;214;244m"
    BG_SELECT   = f"{ESC}[48;2;49;116;143m"
    BG_PANEL    = f"{ESC}[48;2;30;30;46m"
    STRIKE      = f"{ESC}[9m"

# ── Box-drawing characters ──
TL, TR, BL, BR = "╭", "╮", "╰", "╯"
ML, MR = "├", "┤"
H, V = "─", "│"
DIVIDER = f"{TL}{H * 58}{TR}"

# ── Key codes (msvcrt returns bytes; arrow keys are prefixed with 0xe0) ──
class K:
    UP    = b"H"
    DOWN  = b"P"
    LEFT  = b"K"
    RIGHT = b"M"
    ENTER = b"\r"
    ESC   = b"\x1b"
    F1    = b";"
    F2    = b"<"
    F5    = b"="


# ═══════════════════════════════════════════════════════════════
#  Display Helpers
# ═══════════════════════════════════════════════════════════════

import re as _re
_ANSI_RE = _re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub('', text)


def get_display_width(text: str) -> int:
    """Calculate visible width: strip ANSI first, then count CJK as 2."""
    clean = strip_ansi(text)
    w = 0
    for ch in clean:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("F", "W") else 1
    return w


def box_line(content: str, width: int = 60) -> str:
    """Create a bordered line with content padded to target inner width.
    2-char indent after left │ border.
    """
    inner_w = width - 8  # "  │  " + content + "  │"
    vis = get_display_width(content)
    pad = max(0, inner_w - vis)
    return f"  {C.GRAY}{V}  {content}{' ' * pad}  {C.GRAY}{V}{C.RESET}"


def box_line_select(content: str, selected: bool, width: int = 60) -> str:
    """Create a bordered line with optional selection highlight.
    2-char indent after left │ border.
    """
    inner_w = width - 8
    if selected:
        vis = get_display_width(content)
        pad = max(0, inner_w - vis)
        return f"  {C.GRAY}{V}  {C.RESET}{C.BG_SELECT}{C.BOLD}{content}{' ' * pad}{C.RESET}  {C.GRAY}{V}{C.RESET}"
    else:
        return box_line(content, width)


def section_divider(title: str, width: int = 60) -> str:
    """Create a divider line with title embedded: ─── 标题 ────────
    2-char indent after left │ border, matching box_line.
    """
    inner_w = width - 8  # "  │  " + content + "  │"
    if title:
        title_part = f"─── {title} ───"
        title_vis = 3 + 1 + get_display_width(title) + 1 + 3
        remaining = max(0, inner_w - title_vis)
        line_content = f"{title_part}{H * remaining}"
    else:
        line_content = f"{H * inner_w}"
    content = f"{C.GRAY}{line_content}{C.RESET}"
    vis = get_display_width(content)
    pad = max(0, inner_w - vis)
    return f"  {C.GRAY}{V}  {content}{' ' * pad}  {C.GRAY}{V}{C.RESET}"


def clear_screen():
    sys.stdout.write(f"{ESC}[H{ESC}[J")


def hide_cursor():
    sys.stdout.write(f"{ESC}[?25l")
    sys.stdout.flush()


def show_cursor():
    sys.stdout.write(f"{ESC}[?25h")
    sys.stdout.flush()


def move_to(row: int, col: int):
    sys.stdout.write(f"{ESC}[{row};{col}H")


def get_key():
    """Read a keypress. Returns bytes. Arrow keys are 2-byte sequences."""
    key = msvcrt.getch()
    if key in (b"\xe0", b"\x00"):
        return msvcrt.getch()
    return key


def drain_keyboard():
    """Flush keyboard buffer to prevent ghost presses."""
    while msvcrt.kbhit():
        msvcrt.getch()


# ═══════════════════════════════════════════════════════════════
#  Config Manager
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "reply": {
        "content": "收到",
        "delay_min": 1.0,
        "delay_max": 3.0,
        "scan_interval": 2.0
    },
    "groups": []
}


class Config:
    """Manages config.json with auto-generation, field repair, and atomic writes."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._last_mtime = 0.0
        self.data = {}
        self._load_or_generate()

    def _load_or_generate(self):
        if not self.path.exists():
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (json.JSONDecodeError, IOError):
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self._save()
            return
        self._repair(self.data, DEFAULT_CONFIG)
        self._save()

    def _repair(self, target: dict, defaults: dict):
        """Fill missing fields from defaults without overwriting existing values."""
        changed = False
        for key, val in defaults.items():
            if key not in target:
                target[key] = copy.deepcopy(val)
                changed = True
            elif isinstance(val, dict) and isinstance(target[key], dict):
                self._repair(target[key], val)
        if changed:
            self._save()

    def _save(self):
        """Atomic write via temp file + rename."""
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(self.path))
            self._last_mtime = self.path.stat().st_mtime
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def save(self):
        self._save()

    def hot_reload(self):
        """Reload if file changed externally."""
        if not self.path.exists():
            return
        mtime = self.path.stat().st_mtime
        if mtime > self._last_mtime:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                self._last_mtime = mtime
            except (json.JSONDecodeError, IOError):
                pass

    # ── Convenience accessors ──

    def get_reply(self, key: str, default=None):
        return self.data.get("reply", {}).get(key, default)

    def set_reply(self, key: str, value):
        self.data.setdefault("reply", {})[key] = value
        self._save()

    def get_groups(self) -> list:
        return self.data.get("groups", [])

    def add_group(self, name: str):
        groups = self.data.setdefault("groups", [])
        groups.append({
            "name": name,
            "enabled": True,
            "message_region": None,
            "reply_region": None
        })
        self._save()

    def remove_group(self, index: int):
        groups = self.get_groups()
        if 0 <= index < len(groups):
            groups.pop(index)
            self._save()

    def set_group_region(self, index: int, region_type: str, region: dict):
        """Set message_region or reply_region for a group."""
        groups = self.get_groups()
        if 0 <= index < len(groups):
            groups[index][region_type] = region
            self._save()

    def toggle_group(self, index: int):
        groups = self.get_groups()
        if 0 <= index < len(groups):
            groups[index]["enabled"] = not groups[index]["enabled"]
            self._save()


# ═══════════════════════════════════════════════════════════════
#  Region Selector (tkinter overlay)
# ═══════════════════════════════════════════════════════════════

class RegionSelector:
    """
    Full-screen screenshot overlay for selecting screen regions.
    Opens a tkinter window, user drags a rectangle, returns (x, y, w, h).
    Returns None if cancelled (Escape).
    """

    def select(self, title: str = "拖拽选择区域") -> dict | None:
        try:
            import tkinter as tk
        except ImportError:
            return None

        result = {"rect": None}

        # Capture screen
        screenshot = pyautogui.screenshot()

        root = tk.Tk()
        root.title(title)
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)

        from PIL import ImageTk
        photo = ImageTk.PhotoImage(screenshot)

        canvas = tk.Canvas(root, cursor="cross", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=photo)

        # Semi-transparent overlay hint
        canvas.create_text(
            root.winfo_screenwidth() // 2, 30,
            text=f"{title}  |  拖拽选择  |  ESC 取消",
            fill="white", font=("Consolas", 14)
        )

        state = {"x0": 0, "y0": 0, "rect_id": None}

        def on_press(event):
            state["x0"] = event.x
            state["y0"] = event.y
            if state["rect_id"]:
                canvas.delete(state["rect_id"])
            state["rect_id"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="#89B4FA", width=2, dash=(6, 4)
            )

        def on_drag(event):
            if state["rect_id"]:
                canvas.coords(state["rect_id"], state["x0"], state["y0"], event.x, event.y)

        def on_release(event):
            x1, y1 = min(state["x0"], event.x), min(state["y0"], event.y)
            x2, y2 = max(state["x0"], event.x), max(state["y0"], event.y)
            w, h = x2 - x1, y2 - y1
            if w > 10 and h > 10:
                result["rect"] = {"x": x1, "y": y1, "w": w, "h": h}
            root.destroy()

        def on_escape(event):
            root.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", on_escape)

        root.mainloop()
        return result["rect"]


# ═══════════════════════════════════════════════════════════════
#  Monitor Engine
# ═══════════════════════════════════════════════════════════════

class Monitor:
    """
    Background OCR monitor: scans message regions for @all, auto-replies.
    Runs in a daemon thread. Shares stats dict with TUI for live display.
    """

    TRIGGER_PATTERNS = ["@所有人", "@all", "@All", "@ALL"]

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
        # Track recently triggered groups to avoid duplicate replies
        self._recent_triggers: dict[str, float] = {}  # group_name -> timestamp

    def _init_ocr(self):
        """Lazy-init winocr (lightweight, uses Windows built-in OCR engine)."""
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

            # Wait for next scan
            self._stop_event.wait(timeout=scan_interval)

        self.stats["status"] = "Stopped"

    def _scan_group(self, group: dict):
        """Scan one group's message region for @all trigger."""
        region = group["message_region"]
        name = group["name"]

        try:
            # Capture the message region as PIL Image
            screenshot = pyautogui.screenshot(region=(
                region["x"], region["y"], region["w"], region["h"]
            ))

            # Run OCR via winocr (Windows built-in engine, no torch dependency)
            result = self._ocr_engine.recognize_pil_sync(screenshot, "zh-Hans-CN")
            all_text = result.get("text", "")

            # Check for @all trigger
            triggered = any(pat in all_text for pat in self.TRIGGER_PATTERNS)

            if triggered:
                # Debounce: don't re-trigger for same group within 30s
                now = time.time()
                last = self._recent_triggers.get(name, 0)
                if now - last < 30:
                    return

                self._recent_triggers[name] = now
                self.stats["triggers"] += 1
                self.stats["last_trigger"] = f"{name} @ {datetime.now().strftime('%H:%M:%S')}"

                # Auto-reply
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

        # Random delay
        delay = random.uniform(delay_min, delay_max)
        self.stats["status"] = f"等待 {delay:.1f} 秒后回复..."
        time.sleep(delay)

        try:
            # Click the reply input area (center of region)
            click_x = region["x"] + region["w"] // 2
            click_y = region["y"] + region["h"] // 2
            pyautogui.click(click_x, click_y)
            time.sleep(0.3)

            # Copy content to clipboard (supports Chinese)
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(content, win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
            except ImportError:
                # Fallback: use pyperclip or subprocess clip
                import subprocess
                proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
                proc.communicate(content.encode("utf-16-le"))

            # Paste
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.2)

            # Send (Enter)
            pyautogui.press("enter")

            self.stats["replies"] += 1
            self.stats["last_reply"] = f"{group['name']} @ {datetime.now().strftime('%H:%M:%S')}"
            self.stats["status"] = f"已回复 {group['name']}"

        except Exception as e:
            self.stats["errors"] += 1
            self.stats["status"] = f"回复错误: {str(e)[:30]}"


# ═══════════════════════════════════════════════════════════════
#  TUI Application
# ═══════════════════════════════════════════════════════════════

class TUI:
    """Terminal UI with incremental rendering and keyboard navigation.
    Single scrollable panel with all content merged into one view.
    """

    def __init__(self):
        self.config = Config()
        self.monitor = Monitor(self.config)
        self.selector = RegionSelector()

        self.running = True

        # Single cursor for all selectable items
        self.main_selected = 0
        self.reply_editing = False

        # Group action inline overlay state
        self.group_modal_active = False
        self.group_modal_idx = 0
        self._ga_group_idx = -1

        # Region-select wait-for-confirm state
        self._region_wait_active = False
        self._region_wait_msg = ""
        self._region_wait_type = ""  # "message_region" or "reply_region"
        self._region_wait_sel = 0

        # Rendering
        self._last_lines: list[str] = []
        self._term_w = 80
        self._term_h = 25

    def run(self):
        """Main entry: clear screen, auto-start monitor, render loop, restore cursor."""
        atexit_register(show_cursor)
        hide_cursor()
        clear_screen()

        # Auto-start monitoring on launch
        try:
            self.monitor.start()
        except Exception as e:
            self.monitor.stats["status"] = f"启动失败: {e}"

        try:
            while self.running:
                self._update_term_size()
                self._render()

                if msvcrt.kbhit():
                    key = get_key()
                    self._handle_key(key)
                else:
                    time.sleep(0.016)  # ~60fps cap
        except KeyboardInterrupt:
            pass
        finally:
            self.monitor.stop()
            show_cursor()
            clear_screen()

    # ── Terminal size ──

    def _update_term_size(self):
        try:
            sz = os.get_terminal_size()
            self._term_w = sz.columns
            self._term_h = sz.lines
        except OSError:
            pass

    # ── Incremental rendering ──

    def _render(self):
        if self._region_wait_active:
            lines = self._render_region_wait()
        else:
            lines = self._render_main()

        if not self._last_lines:
            clear_screen()

        for i, line in enumerate(lines):
            if i >= len(self._last_lines) or line != self._last_lines[i]:
                move_to(i + 1, 1)
                sys.stdout.write(f"{line}{ESC}[K")

        if len(lines) < len(self._last_lines):
            move_to(len(lines) + 1, 1)
            sys.stdout.write(f"{ESC}[J")

        self._last_lines = lines
        sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════
    #  MAIN — Single Unified Panel
    # ═══════════════════════════════════════════════════════════

    def _render_main(self) -> list[str]:
        """Render the single unified panel: stats + reply settings + group management + exit."""
        W = 60
        lines = []
        s = self.monitor.stats
        groups = self.config.get_groups()

        lines.append("")

        # Title embedded in top border: ╭─── AutoReplyer.Kairl ────────╮
        title_text = "AutoReplyer.Kairl"
        title_prefix = f"─── {C.MAUVE}{C.BOLD}{title_text}{C.RESET}{C.GRAY} "
        prefix_vis = 4 + len(title_text) + 1  # "─── " + title + trailing space
        remaining = max(0, W - prefix_vis - 4)
        lines.append(f"  {C.GRAY}{TL}{title_prefix}{H * remaining}{TR}{C.RESET}")

        # ── Monitor stats ──
        running_color = C.GREEN if s["running"] else C.RED
        status_text = "运行中" if s["running"] else "已停止"
        lines.append(box_line(f" {C.DIM}监控状态{C.RESET}    {running_color}{status_text}{C.RESET}", W))

        counters = (
            f" {C.DIM}触发:{C.RESET} {C.YELLOW}{s['triggers']}{C.RESET}       "
            f"{C.DIM}回复:{C.RESET} {C.GREEN}{s['replies']}{C.RESET}       "
            f"{C.DIM}错误:{C.RESET} {C.RED}{s['errors']}{C.RESET}"
        )
        lines.append(box_line(counters, W))
        lines.append(box_line(f" {C.DIM}最后触发{C.RESET}     {C.WHITE}{s['last_trigger']}{C.RESET}", W))
        lines.append(box_line(f" {C.DIM}最后回复{C.RESET}     {C.WHITE}{s['last_reply']}{C.RESET}", W))

        # ── Reply settings section ──
        lines.append(section_divider("回复设置", W))

        reply_items = [
            ("回复内容",     self.config.get_reply("content", ""),         "str"),
            ("最小延迟 (秒)", str(self.config.get_reply("delay_min", 1.0)), "float"),
            ("最大延迟 (秒)", str(self.config.get_reply("delay_max", 3.0)), "float"),
            ("扫描间隔 (秒)", str(self.config.get_reply("scan_interval", 2.0)), "float"),
        ]
        for i, (label, value, _) in enumerate(reply_items):
            sel = i == self.main_selected
            editing = sel and self.reply_editing
            display_val = self._edit_buffer if editing else value

            if editing:
                line = f" ›{C.BOLD} {label}:{C.RESET} {C.YELLOW}{display_val}_{C.RESET}"
            elif sel:
                line = f" ›{C.BOLD} {label}:{C.RESET} {C.GREEN}{value}{C.RESET}"
            else:
                line = f"  {C.WHITE}{label}:{C.RESET} {C.WHITE}{value}{C.RESET}"
            lines.append(box_line(line, W))

        # ── Group settings section ──
        lines.append(section_divider("群设置", W))

        if not groups:
            lines.append(box_line(f" {C.DIM}(暂无群组配置){C.RESET}", W))
        else:
            for i, group in enumerate(groups):
                group_idx = 4 + i
                sel = group_idx == self.main_selected
                enabled = group.get("enabled", True)
                has_msg = group.get("message_region") is not None
                has_rpl = group.get("reply_region") is not None

                # Status indicators
                status_parts = []
                if enabled:
                    status_parts.append(f"{C.GREEN}启用{C.RESET}")
                else:
                    status_parts.append(f"{C.RED}停用{C.RESET}")
                if has_msg:
                    status_parts.append(f"{C.GREEN}消息{C.RESET}")
                else:
                    status_parts.append(f"{C.DIM}----{C.RESET}")
                if has_rpl:
                    status_parts.append(f"{C.GREEN}回复{C.RESET}")
                else:
                    status_parts.append(f"{C.DIM}----{C.RESET}")

                status_str = " ".join(status_parts)
                name = group["name"]
                line = f" {C.WHITE}{name}{C.RESET}  [{status_str}]"
                lines.append(box_line_select(line, sel, W))

                # Render group action overlay if active for this group
                if self.group_modal_active and self._ga_group_idx == i:
                    group_actions = [
                        "停用监视" if enabled else "启用监视",
                        "设置消息区域",
                        "设置回复区域",
                        "删除此群"
                    ]
                    for j, action in enumerate(group_actions):
                        action_sel = j == self.group_modal_idx
                        if action_sel:
                            action_label = f" ›{C.BOLD} {action}{C.RESET}"
                        else:
                            action_label = f"  {C.WHITE}{action}{C.RESET}"
                        lines.append(box_line_select(action_label, action_sel, W))

        # "+ 添加新群"
        add_idx = 4 + len(groups)
        add_sel = self.main_selected == add_idx
        if add_sel:
            add_label = f" ›{C.BOLD} + 添加新群{C.RESET}"
        else:
            add_label = f"  {C.WHITE}+ 添加新群{C.RESET}"
        lines.append(box_line_select(add_label, add_sel, W))

        # ── Separator before exit ──
        lines.append(section_divider("", W))

        # "退出" — right-aligned per design spec
        exit_idx = 4 + len(groups) + 1
        exit_sel = self.main_selected == exit_idx
        exit_text = "退出"
        exit_inner_w = W - 4
        exit_text_w = get_display_width(exit_text)
        if exit_sel:
            ptr_w = 2  # "› "
            right_pad = 2
            left_pad = max(0, exit_inner_w - ptr_w - exit_text_w - right_pad)
            exit_label = f"{' ' * left_pad}› {C.BOLD}{exit_text}{C.RESET}{' ' * right_pad}"
        else:
            right_pad = 2
            left_pad = max(0, exit_inner_w - exit_text_w - right_pad)
            exit_label = f"{' ' * left_pad}{C.WHITE}{exit_text}{C.RESET}{' ' * right_pad}"
        lines.append(box_line_select(exit_label, exit_sel, W))

        # ── Bottom border ──
        lines.append(f"  {C.GRAY}{BL}{H * (W - 4)}{BR}{C.RESET}")
        lines.append("")
        return lines

    # ═══════════════════════════════════════════════════════════
    #  Key Handling
    # ═══════════════════════════════════════════════════════════

    def _handle_key(self, key: bytes):
        """Route all keypresses based on current state."""
        if self._region_wait_active:
            self._handle_region_wait(key)
        else:
            self._handle_main(key)

    # ── Main handler ──

    def _handle_main(self, key: bytes):
        """Handle keyboard input on the unified single panel.
        Handles reply editing, group modal navigation, add group, and exit.
        """
        if self.reply_editing:
            self._handle_reply_edit(key)
            return

        groups = self.config.get_groups()
        n = 4 + len(groups) + 2  # 4 reply items + N groups + add + exit

        if self.group_modal_active:
            # Modal navigation: 4 items (toggle, set msg, set reply, delete)
            if key == K.UP:
                self.group_modal_idx = (self.group_modal_idx - 1) % 4
            elif key == K.DOWN:
                self.group_modal_idx = (self.group_modal_idx + 1) % 4
            elif key == K.ENTER:
                self._execute_group_modal_action()
            elif key == K.ESC or key == b"\x08":
                self.group_modal_active = False
            return

        if key == K.UP:
            self.main_selected = (self.main_selected - 1) % n
        elif key == K.DOWN:
            self.main_selected = (self.main_selected + 1) % n
        elif key == K.ENTER:
            if self.main_selected < 4:
                # Reply item → enter edit mode
                self.reply_editing = True
                self._start_reply_edit()
            elif self.main_selected < 4 + len(groups):
                # Group → open inline overlay
                self._ga_group_idx = self.main_selected - 4
                self.group_modal_active = True
                self.group_modal_idx = 0
            elif self.main_selected == 4 + len(groups):
                # Add group
                self._add_group()
                # Adjust cursor after add
                new_n = 4 + len(self.config.get_groups()) + 2
                self.main_selected = min(self.main_selected, new_n - 1)
            else:
                # Exit
                self.running = False
        elif key == K.ESC or key == b"\x08":
            self.running = False

    # ── Group modal action ──

    def _execute_group_modal_action(self):
        """Execute the selected inline overlay action for the current group."""
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            self.group_modal_active = False
            return

        idx = self._ga_group_idx

        if self.group_modal_idx == 0:
            # Toggle enable/disable
            self.config.toggle_group(idx)
        elif self.group_modal_idx == 1:
            # Set message region
            self._select_region("message_region")
        elif self.group_modal_idx == 2:
            # Set reply region
            self._select_region("reply_region")
        elif self.group_modal_idx == 3:
            # Delete group
            self.config.remove_group(idx)
            n = 4 + len(self.config.get_groups()) + 2
            self.main_selected = min(self.main_selected, max(0, n - 1))

        self.group_modal_active = False

    # ── Reply editing ──

    def _start_reply_edit(self):
        """Enter edit mode for the selected reply setting."""
        self._edit_buffer = ""
        self._edit_cursor = 0
        keys = ["content", "delay_min", "delay_max", "scan_interval"]
        if self.main_selected < len(keys):
            val = self.config.get_reply(keys[self.main_selected], "")
            self._edit_buffer = str(val)
            self._edit_cursor = len(self._edit_buffer)

    def _handle_reply_edit(self, key: bytes):
        """Handle keyboard input during reply setting edit mode.
        Uses getwch() internally for proper Chinese input support.
        """
        # Use getwch() for the actual input character
        ch = msvcrt.getwch()

        if ch == "\r":
            self._commit_reply_edit()
            return
        if ch == "\x1b":
            self.reply_editing = False
            return

        # Special key prefix — read the actual key code
        if ch == "\x00":
            real = msvcrt.getwch()
            # Ignore arrow keys etc. during editing
            return

        # Backspace
        if ch == "\x08":
            if self._edit_cursor > 0:
                self._edit_buffer = self._edit_buffer[:self._edit_cursor-1] + self._edit_buffer[self._edit_cursor:]
                self._edit_cursor -= 1
            return

        if ch.isprintable():
            self._edit_buffer = self._edit_buffer[:self._edit_cursor] + ch + self._edit_buffer[self._edit_cursor:]
            self._edit_cursor += 1

    def _commit_reply_edit(self):
        """Save the edited value back to config."""
        keys = ["content", "delay_min", "delay_max", "scan_interval"]
        if self.main_selected >= len(keys):
            return

        key = keys[self.main_selected]
        val = self._edit_buffer.strip()

        if key == "content":
            self.config.set_reply(key, val)
        else:
            try:
                num = float(val)
                if num > 0:
                    self.config.set_reply(key, num)
            except ValueError:
                pass

        self.reply_editing = False

    # ── Add group (state-based, incremental rendering) ──

    def _add_group(self):
        """Auto-create a new group named 群 N."""
        existing = self.config.get_groups()
        # Find next available number
        used = set()
        for g in existing:
            name = g.get("name", "")
            if name.startswith("群 "):
                try:
                    used.add(int(name[2:]))
                except ValueError:
                    pass
        n = 1
        while n in used:
            n += 1
        self.config.add_group(f"群 {n}")

    def _render_region_wait(self) -> list[str]:
        """Render the region-select confirmation screen."""
        W = 60
        lines = []
        lines.append("")

        title_text = self._region_wait_msg
        title_prefix = f"─── {C.BLUE}{C.BOLD}{title_text}{C.RESET}{C.GRAY} "
        prefix_vis = 4 + get_display_width(title_text) + 1
        remaining = max(0, W - prefix_vis - 4)
        lines.append(f"  {C.GRAY}{TL}{title_prefix}{H * remaining}{TR}{C.RESET}")

        lines.append(box_line(f" {C.DIM}即将显示全屏截图{C.RESET}", W))
        lines.append(box_line(f" {C.DIM}拖拽鼠标框选目标区域{C.RESET}", W))
        lines.append(box_line(f" {C.YELLOW}按 Enter 开始框选，Esc 取消{C.RESET}", W))

        lines.append(section_divider("", W))

        lines.append(box_line(f" ›{C.BOLD} 开始框选{C.RESET}", W))
        lines.append(box_line(f"  {C.WHITE}取消{C.RESET}", W))

        hint = f"{C.DIM}[Up/Down] 导航    [Enter] 确认    [Esc] 返回{C.RESET}"
        lines.append(box_line(f" {hint}", W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 4)}{BR}{C.RESET}")
        lines.append("")
        return lines

    def _select_region(self, region_type: str):
        """Enter region-select wait state. Shows instructions in TUI box."""
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            return
        group = groups[self._ga_group_idx]
        type_name = "消息区域" if region_type == "message_region" else "回复输入区域"
        self._region_wait_active = True
        self._region_wait_type = region_type
        self._region_wait_msg = f"框选 [{group['name']}] 的{type_name}"
        self._last_lines = []

    def _render_region_wait(self) -> list[str]:
        """Render the region-select confirmation screen."""
        W = 60
        lines = []
        lines.append("")

        title_text = self._region_wait_msg
        title_prefix = f"─── {C.BLUE}{C.BOLD}{title_text}{C.RESET}{C.GRAY} "
        prefix_vis = 4 + get_display_width(title_text) + 1
        remaining = max(0, W - prefix_vis - 4)
        lines.append(f"  {C.GRAY}{TL}{title_prefix}{H * remaining}{TR}{C.RESET}")

        lines.append(box_line(f" {C.DIM}即将显示全屏截图{C.RESET}", W))
        lines.append(box_line(f" {C.DIM}拖拽鼠标框选目标区域{C.RESET}", W))
        lines.append(box_line(f" {C.YELLOW}按 Enter 开始框选，Esc 取消{C.RESET}", W))

        # Separator
        lines.append(section_divider("", W))

        # Actions as cursor-selectable items
        lines.append(box_line(f" ›{C.BOLD} 开始框选{C.RESET}", W))
        lines.append(box_line(f"  {C.WHITE}取消{C.RESET}", W))

        # Hint bar
        hint = f"{C.DIM}[Up/Down] 导航    [Enter] 确认    [Esc] 返回{C.RESET}"
        lines.append(box_line(f" {hint}", W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 4)}{BR}{C.RESET}")
        lines.append("")
        return lines

    def _handle_region_wait(self, key: bytes):
        """Handle keyboard input during region-select wait state."""
        # Two items: 0=start, 1=cancel
        if key == K.UP:
            self._region_wait_sel = (self._region_wait_sel - 1) % 2
        elif key == K.DOWN:
            self._region_wait_sel = (self._region_wait_sel + 1) % 2
        elif key == K.ENTER:
            if self._region_wait_sel == 0:
                self._do_region_select()
            else:
                self._region_wait_active = False
                self._last_lines = []
            self._region_wait_sel = 0
        elif key == K.ESC:
            self._region_wait_active = False
            self._last_lines = []
            self._region_wait_sel = 0

    def _do_region_select(self):
        """Execute the actual tkinter region selector overlay."""
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            self._region_wait_active = False
            self._last_lines = []
            return

        group = groups[self._ga_group_idx]
        title = self._region_wait_msg

        # Temporarily show cursor for tkinter
        show_cursor()
        rect = self.selector.select(title=title)
        hide_cursor()

        if rect:
            self.config.set_group_region(self._ga_group_idx, self._region_wait_type, rect)

        self._region_wait_active = False
        self._last_lines = []


# ═══════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════

def atexit_register(func):
    """Register atexit handler."""
    import atexit
    atexit.register(func)


def main():
    # Configure pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05

    app = TUI()
    app.run()


if __name__ == "__main__":
    main()
