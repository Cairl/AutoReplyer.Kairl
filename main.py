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
    GRAY        = f"{ESC}[38;2;108;112;134m"
    SUBTEXT     = f"{ESC}[38;2;186;194;222m"
    WHITE       = f"{ESC}[38;2;205;214;244m"
    BG_SELECT   = f"{ESC}[48;2;49;116;143m"
    BG_PANEL    = f"{ESC}[48;2;30;30;46m"
    STRIKE      = f"{ESC}[9m"

# ── Box-drawing characters ──
TL, TR, BL, BR = "╭", "╮", "╰", "╯"
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
    """Create a bordered line with content padded to target inner width."""
    inner_w = width - 4  # "│ " + content + " │"
    vis = get_display_width(content)
    pad = max(0, inner_w - vis)
    return f"  {C.GRAY}{V} {content}{' ' * pad} {V}{C.RESET}"


def box_line_select(content: str, selected: bool, width: int = 60) -> str:
    """Create a bordered line with optional selection highlight."""
    inner_w = width - 4
    if selected:
        vis = get_display_width(content)
        pad = max(0, inner_w - vis)
        return f"  {C.GRAY}{V}{C.RESET}{C.BG_SELECT}{C.BOLD} {content}{' ' * pad} {C.RESET}{C.GRAY}{V}{C.RESET}"
    else:
        return box_line(content, width)


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
    """Terminal UI with incremental rendering and keyboard navigation."""

    # ── Screens ──
    MAIN          = "main"
    REPLY         = "reply"
    GROUPS        = "groups"
    GROUP_ACTIONS = "group_actions"
    MONITOR       = "monitor"

    def __init__(self):
        self.config = Config()
        self.monitor = Monitor(self.config)
        self.selector = RegionSelector()

        self.screen = self.MAIN
        self.running = True

        # Main menu
        self.main_selected = 0

        # Reply settings
        self.reply_selected = 0
        self.reply_editing = False

        # Group management
        self.group_selected = 0    # index into groups list (+ "添加新群" / "返回")
        self.ga_selected = 0       # group actions submenu cursor

        # Monitor
        self.mon_selected = 0      # 0=toggle button, 1=返回

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
        lines = self._get_render_lines()

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

    def _get_render_lines(self) -> list[str]:
        if self.screen == self.MAIN:
            return self._render_main()
        elif self.screen == self.REPLY:
            return self._render_reply()
        elif self.screen == self.GROUPS:
            return self._render_groups()
        elif self.screen == self.GROUP_ACTIONS:
            return self._render_group_actions()
        elif self.screen == self.MONITOR:
            return self._render_monitor()
        return []

    # ── Main menu ──

    _MAIN_ITEMS = ["回复设置", "群管理", "监控面板", "退出"]

    def _render_main(self) -> list[str]:
        W = 60
        lines = []

        lines.append("")
        lines.append(f"  {C.MAUVE}{C.BOLD}AutoReplyer.Kairl{C.RESET}")
        lines.append(f"  {C.DIM}微信群 @所有人 自动回复工具{C.RESET}")
        lines.append("")
        lines.append(f"  {C.GRAY}{TL}{H * (W - 2)}{TR}{C.RESET}")

        for i, item in enumerate(self._MAIN_ITEMS):
            sel = i == self.main_selected
            label = f"  {item}"
            if sel:
                label = f" ›{C.BOLD} {item}{C.RESET}"
            lines.append(box_line_select(label, sel, W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 2)}{BR}{C.RESET}")
        lines.append("")
        lines.append(f"  {C.DIM}[Up/Down] 导航    [Enter] 确认    [Esc] 退出{C.RESET}")

        # Status bar
        groups = self.config.get_groups()
        enabled = sum(1 for g in groups if g.get("enabled", True))
        status = f"群组: {enabled}/{len(groups)} 个已启用"
        if self.monitor.stats["running"]:
            status += f"  {C.GREEN}[监控中]{C.RESET}"
        lines.append(f"  {C.DIM}{status}{C.RESET}")

        return lines

    # ── Reply settings ──

    def _render_reply(self) -> list[str]:
        W = 60
        lines = []

        lines.append("")
        lines.append(f"  {C.BLUE}{C.BOLD}回复设置{C.RESET}")
        lines.append("")
        lines.append(f"  {C.GRAY}{TL}{H * (W - 2)}{TR}{C.RESET}")

        # Build items (4 settings + "返回")
        items = [
            ("回复内容",     self.config.get_reply("content", ""),         "str"),
            ("最小延迟 (秒)", str(self.config.get_reply("delay_min", 1.0)), "float"),
            ("最大延迟 (秒)", str(self.config.get_reply("delay_max", 3.0)), "float"),
            ("扫描间隔 (秒)", str(self.config.get_reply("scan_interval", 2.0)), "float"),
        ]

        for i, (label, value, _) in enumerate(items):
            sel = i == self.reply_selected
            editing = sel and self.reply_editing

            # When editing, show live buffer instead of saved config value
            display_val = self._edit_buffer if editing else value

            if editing:
                line = f"  {C.BOLD}{C.TEAL}[>]{C.RESET} {C.WHITE}{label}:{C.RESET} {C.YELLOW}{display_val}_"
            elif sel:
                line = f" ›{C.BOLD} {label}:{C.RESET} {C.GREEN}{value}{C.RESET}"
            else:
                line = f"   {C.SUBTEXT}{label}:{C.RESET} {C.WHITE}{value}{C.RESET}"

            lines.append(box_line(line, W))

        lines.append(f"  {C.GRAY}{H * W}{C.RESET}")
        lines.append(f"  {C.DIM}内容: 回复文本 (支持中文){C.RESET}")
        lines.append(f"  {C.DIM}延迟: 回复前随机等待 (秒){C.RESET}")
        lines.append(f"  {C.DIM}间隔: 扫描 @所有人 的频率 (秒){C.RESET}")

        # "返回" item
        back_sel = self.reply_selected == 4
        back_label = f"  返回"
        if back_sel:
            back_label = f" ›{C.BOLD} 返回{C.RESET}"
        lines.append(box_line_select(back_label, back_sel, W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 2)}{BR}{C.RESET}")
        lines.append("")
        lines.append(f"  {C.DIM}[Up/Down] 导航    [Enter] 编辑/返回    [Esc] 返回{C.RESET}")

        return lines

    # ── Group management ──

    def _render_groups(self) -> list[str]:
        W = 60
        lines = []
        groups = self.config.get_groups()
        # Total items: groups + "添加新群" + "返回"
        n_total = len(groups) + 2

        lines.append("")
        lines.append(f"  {C.TEAL}{C.BOLD}群管理{C.RESET}")
        lines.append("")
        lines.append(f"  {C.GRAY}{TL}{H * (W - 2)}{TR}{C.RESET}")

        if not groups:
            lines.append(box_line(f"  {C.DIM}(暂无群组配置){C.RESET}", W))
        else:
            for i, group in enumerate(groups):
                sel = i == self.group_selected
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

                status = " ".join(status_parts)
                name = group["name"]
                line = f"  {name}  [{status}]"
                lines.append(box_line_select(line, sel, W))

        lines.append(f"  {C.GRAY}{H * W}{C.RESET}")

        # "添加新群" item
        add_idx = len(groups)
        add_sel = self.group_selected == add_idx
        add_label = f"  + 添加新群"
        if add_sel:
            add_label = f" ›{C.BOLD} + 添加新群{C.RESET}"
        lines.append(box_line_select(add_label, add_sel, W))

        # "返回" item
        back_idx = len(groups) + 1
        back_sel = self.group_selected == back_idx
        back_label = f"  返回"
        if back_sel:
            back_label = f" ›{C.BOLD} 返回{C.RESET}"
        lines.append(box_line_select(back_label, back_sel, W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 2)}{BR}{C.RESET}")
        lines.append("")
        lines.append(f"  {C.DIM}[Up/Down] 导航    [Enter] 选择    [Esc] 返回{C.RESET}")

        return lines

    # ── Group actions submenu ──

    def _render_group_actions(self) -> list[str]:
        W = 60
        lines = []
        groups = self.config.get_groups()

        if self._ga_group_idx >= len(groups):
            return ["  群组不存在"]

        group = groups[self._ga_group_idx]
        enabled = group.get("enabled", True)
        has_msg = group.get("message_region") is not None
        has_rpl = group.get("reply_region") is not None

        # Build action items
        toggle_label = "停用监视" if enabled else "启用监视"
        actions = [
            (toggle_label,    True),
            ("设置消息区域",   True),
            ("设置回复区域",   True),
            ("删除此群",      True),
            ("返回",          True),
        ]

        lines.append("")
        lines.append(f"  {C.TEAL}{C.BOLD}{group['name']}{C.RESET}  {C.DIM}群组操作{C.RESET}")
        lines.append("")
        lines.append(f"  {C.GRAY}{TL}{H * (W - 2)}{TR}{C.RESET}")

        for i, (label, _) in enumerate(actions):
            sel = i == self.ga_selected
            line = f"  {label}"
            if sel:
                line = f" ›{C.BOLD} {label}{C.RESET}"
            lines.append(box_line_select(line, sel, W))

        lines.append(f"  {C.GRAY}{H * W}{C.RESET}")

        # Current status
        st_parts = []
        st_parts.append(f"状态: {C.GREEN}启用{C.RESET}" if enabled else f"状态: {C.RED}停用{C.RESET}")
        st_parts.append(f"消息区: {C.GREEN}已设{C.RESET}" if has_msg else f"消息区: {C.YELLOW}未设{C.RESET}")
        st_parts.append(f"回复区: {C.GREEN}已设{C.RESET}" if has_rpl else f"回复区: {C.YELLOW}未设{C.RESET}")
        lines.append(box_line(f"  {'  '.join(st_parts)}", W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 2)}{BR}{C.RESET}")
        lines.append("")
        lines.append(f"  {C.DIM}[Up/Down] 导航    [Enter] 执行    [Esc] 返回{C.RESET}")

        return lines

    # ── Monitor screen ──

    def _render_monitor(self) -> list[str]:
        W = 60
        lines = []
        s = self.monitor.stats

        lines.append("")
        color = C.GREEN if s["running"] else C.GRAY
        lines.append(f"  {color}{C.BOLD}监控面板{C.RESET}  {C.DIM}[{s['status']}]{C.RESET}")
        lines.append("")
        lines.append(f"  {C.GRAY}{TL}{H * (W - 2)}{TR}{C.RESET}")

        # Stats
        stats_items = [
            ("状态",     s["status"]),
            ("扫描次数", str(s["scans"])),
            ("触发次数", f"{C.YELLOW}{s['triggers']}{C.RESET}" if s["triggers"] else "0"),
            ("回复次数", f"{C.GREEN}{s['replies']}{C.RESET}" if s["replies"] else "0"),
            ("错误次数", f"{C.RED}{s['errors']}{C.RESET}" if s["errors"] else "0"),
            ("最后触发", s["last_trigger"]),
            ("最后回复", s["last_reply"]),
        ]

        for label, value in stats_items:
            line = f"  {C.SUBTEXT}{label}:{C.RESET} {C.WHITE}{value}{C.RESET}"
            lines.append(box_line(line, W))

        lines.append(f"  {C.GRAY}{H * W}{C.RESET}")

        # Monitored groups
        groups = self.config.get_groups()
        active = [g for g in groups if g.get("enabled") and g.get("message_region")]
        lines.append(box_line(f"  {C.DIM}监视群组: {len(active)} 个{C.RESET}", W))

        for g in active[:5]:
            lines.append(box_line(f"    {C.WHITE}{g['name']}{C.RESET}", W))

        lines.append(f"  {C.GRAY}{H * W}{C.RESET}")

        # Toggle button
        toggle_label = "停止监控" if s["running"] else "启动监控"
        toggle_sel = self.mon_selected == 0
        line = f"  {toggle_label}"
        if toggle_sel:
            line = f" ›{C.BOLD} {toggle_label}{C.RESET}"
        lines.append(box_line_select(line, toggle_sel, W))

        # "返回" item
        back_sel = self.mon_selected == 1
        back_label = f"  返回"
        if back_sel:
            back_label = f" ›{C.BOLD} 返回{C.RESET}"
        lines.append(box_line_select(back_label, back_sel, W))

        lines.append(f"  {C.GRAY}{BL}{H * (W - 2)}{BR}{C.RESET}")
        lines.append("")
        lines.append(f"  {C.DIM}[Up/Down] 导航    [Enter] 确认    [Esc] 返回{C.RESET}")

        return lines

    # ── Input handling ──

    def _handle_key(self, key: bytes):
        if self.screen == self.MAIN:
            self._handle_main(key)
        elif self.screen == self.REPLY:
            self._handle_reply(key)
        elif self.screen == self.GROUPS:
            self._handle_groups(key)
        elif self.screen == self.GROUP_ACTIONS:
            self._handle_group_actions(key)
        elif self.screen == self.MONITOR:
            self._handle_monitor(key)

    def _handle_main(self, key: bytes):
        n = len(self._MAIN_ITEMS)
        if key == K.UP:
            self.main_selected = (self.main_selected - 1) % n
        elif key == K.DOWN:
            self.main_selected = (self.main_selected + 1) % n
        elif key == K.ENTER:
            idx = self.main_selected
            if idx == 0:
                self.screen = self.REPLY
                self.reply_selected = 0
                self.reply_editing = False
                self._last_lines = []
            elif idx == 1:
                self.screen = self.GROUPS
                self.group_selected = 0
                self._last_lines = []
            elif idx == 2:
                self.screen = self.MONITOR
                self.mon_selected = 0
                self._last_lines = []
            elif idx == 3:
                self.running = False
        elif key == K.ESC:
            self.running = False

    def _handle_reply(self, key: bytes):
        if self.reply_editing:
            self._handle_reply_edit(key)
            return

        n = 5  # 4 settings + "返回"
        if key == K.UP:
            self.reply_selected = (self.reply_selected - 1) % n
        elif key == K.DOWN:
            self.reply_selected = (self.reply_selected + 1) % n
        elif key == K.ENTER:
            if self.reply_selected == 4:  # "返回"
                self.screen = self.MAIN
                self._last_lines = []
            else:
                self.reply_editing = True
                self._start_reply_edit()
        elif key == K.ESC:
            self.screen = self.MAIN
            self._last_lines = []

    def _start_reply_edit(self):
        """Enter edit mode for the selected reply setting."""
        self._edit_buffer = ""
        self._edit_cursor = 0
        keys = ["content", "delay_min", "delay_max", "scan_interval"]
        if self.reply_selected < len(keys):
            val = self.config.get_reply(keys[self.reply_selected], "")
            self._edit_buffer = str(val)
            self._edit_cursor = len(self._edit_buffer)

    def _handle_reply_edit(self, key: bytes):
        if key == K.ENTER:
            self._commit_reply_edit()
            return
        if key == K.ESC:
            self.reply_editing = False
            return

        # Backspace
        if key == b"\x08":
            if self._edit_cursor > 0:
                self._edit_buffer = self._edit_buffer[:self._edit_cursor-1] + self._edit_buffer[self._edit_cursor:]
                self._edit_cursor -= 1
            return

        # Non-ASCII (Chinese etc.)
        if key[0] > 127 or key == b"\x00" or key == b"\xe0":
            try:
                ch = key.decode("utf-8", errors="ignore")
            except Exception:
                return
        else:
            ch = key.decode("ascii", errors="ignore")

        if not ch or not ch.isprintable():
            return

        self._edit_buffer = self._edit_buffer[:self._edit_cursor] + ch + self._edit_buffer[self._edit_cursor:]
        self._edit_cursor += 1

    def _commit_reply_edit(self):
        """Save the edited value back to config."""
        keys = ["content", "delay_min", "delay_max", "scan_interval"]
        if self.reply_selected >= len(keys):
            return

        key = keys[self.reply_selected]
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

    def _handle_groups(self, key: bytes):
        groups = self.config.get_groups()
        n_total = len(groups) + 2  # groups + "添加新群" + "返回"

        if key == K.UP:
            self.group_selected = (self.group_selected - 1) % n_total
        elif key == K.DOWN:
            self.group_selected = (self.group_selected + 1) % n_total
        elif key == K.ENTER:
            if self.group_selected < len(groups):
                # Selected an existing group → open actions submenu
                self._ga_group_idx = self.group_selected
                self.ga_selected = 0
                self.screen = self.GROUP_ACTIONS
                self._last_lines = []
            elif self.group_selected == len(groups):
                # "添加新群"
                self._add_group()
            else:
                # "返回"
                self.screen = self.MAIN
                self._last_lines = []
        elif key == K.ESC:
            self.screen = self.MAIN
            self._last_lines = []

    def _handle_group_actions(self, key: bytes):
        n = 5  # 5 actions
        if key == K.UP:
            self.ga_selected = (self.ga_selected - 1) % n
        elif key == K.DOWN:
            self.ga_selected = (self.ga_selected + 1) % n
        elif key == K.ENTER:
            self._execute_group_action()
        elif key == K.ESC:
            self.screen = self.GROUPS
            self._last_lines = []

    def _execute_group_action(self):
        """Execute the selected action in the group actions submenu."""
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            self.screen = self.GROUPS
            self._last_lines = []
            return

        idx = self._ga_group_idx

        if self.ga_selected == 0:
            # Toggle enable/disable
            self.config.toggle_group(idx)
        elif self.ga_selected == 1:
            # Set message region
            self._select_region("message_region")
        elif self.ga_selected == 2:
            # Set reply region
            self._select_region("reply_region")
        elif self.ga_selected == 3:
            # Delete group
            self.config.remove_group(idx)
            self.screen = self.GROUPS
            self.group_selected = min(self.group_selected, max(0, len(self.config.get_groups()) + 1))
            self._last_lines = []
        elif self.ga_selected == 4:
            # "返回"
            self.screen = self.GROUPS
            self._last_lines = []

    def _add_group(self):
        """Add a new group via inline input."""
        show_cursor()
        clear_screen()
        print(f"\n  {C.BLUE}{C.BOLD}添加新群{C.RESET}\n")
        print(f"  {C.DIM}输入群名称 (Esc 取消):{C.RESET}")
        print(f"  {C.GRAY}{TL}{H * 40}{TR}{C.RESET}")
        sys.stdout.write(f"  {C.GRAY}{V} {C.RESET}")
        sys.stdout.flush()

        name = self._read_line_input()
        show_cursor()

        if name.strip():
            self.config.add_group(name.strip())
            self.group_selected = len(self.config.get_groups()) - 1

        hide_cursor()
        clear_screen()
        self._last_lines = []

    def _select_region(self, region_type: str):
        """Open region selector for the selected group."""
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            return

        group = groups[self._ga_group_idx]
        type_name = "消息区域" if region_type == "message_region" else "回复输入区域"

        # Show instructions
        show_cursor()
        clear_screen()
        print(f"\n  {C.BLUE}{C.BOLD}框选{type_name}{C.RESET}")
        print(f"  {C.DIM}群组: {group['name']}{C.RESET}")
        print(f"\n  {C.YELLOW}即将显示全屏截图。{C.RESET}")
        print(f"  {C.DIM}拖拽鼠标框选{type_name}，按 Esc 取消。{C.RESET}")
        print(f"\n  {C.DIM}按任意键继续...{C.RESET}")
        sys.stdout.flush()

        get_key()
        hide_cursor()

        title = f"框选 [{group['name']}] 的{type_name}"
        rect = self.selector.select(title=title)

        if rect:
            self.config.set_group_region(self._ga_group_idx, region_type, rect)
            print(f"\n  {C.GREEN}区域已保存: x={rect['x']}, y={rect['y']}, w={rect['w']}, h={rect['h']}{C.RESET}")
            print(f"  {C.DIM}按任意键继续...{C.RESET}")
            get_key()

        clear_screen()
        self._last_lines = []

    def _read_line_input(self) -> str:
        """Read a line of input with support for backspace. Returns on Enter/Esc."""
        result = []
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\r":
                    return "".join(result)
                if ch == "\x1b":
                    return ""
                if ch == "\x08":
                    if result:
                        last = result.pop()
                        w = get_display_width(last)
                        sys.stdout.write("\b" * w + " " * w + "\b" * w)
                        sys.stdout.flush()
                elif ch in ("\x00", "\xe0"):
                    msvcrt.getwch()
                elif ch.isprintable():
                    result.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()
            else:
                time.sleep(0.01)

    def _handle_monitor(self, key: bytes):
        if key == K.UP:
            self.mon_selected = (self.mon_selected - 1) % 2
        elif key == K.DOWN:
            self.mon_selected = (self.mon_selected + 1) % 2
        elif key == K.ENTER:
            if self.mon_selected == 0:
                # Toggle monitoring
                if self.monitor.stats["running"]:
                    self.monitor.stop()
                else:
                    try:
                        self.monitor.start()
                    except RuntimeError as e:
                        self.monitor.stats["status"] = f"错误: {e}"
            else:
                # "返回"
                if self.monitor.stats["running"]:
                    self.monitor.stop()
                self.screen = self.MAIN
                self._last_lines = []
        elif key == K.ESC:
            if self.monitor.stats["running"]:
                self.monitor.stop()
            self.screen = self.MAIN
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
