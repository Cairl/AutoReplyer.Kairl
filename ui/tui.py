"""
TUI Application: incremental rendering, keyboard navigation, all screens.
"""

import os
import sys
import time
import msvcrt

from ui.ui import (
    C, TL, TR, BL, BR, H, V, K, ESC,
    strip_ansi, get_display_width, pad_to,
    clear_screen, hide_cursor, show_cursor, move_to, get_key,
)
from core.config import Config
from core.monitor import Monitor
from core.region import RegionSelector


class TUI:
    """Terminal UI with incremental rendering and keyboard navigation."""

    def __init__(self):
        self.config = Config()
        self.monitor = Monitor(self.config)
        self.selector = RegionSelector()

        self.running = True
        self.main_selected = 0
        self.reply_editing = False

        self._group_action_indices = {}  # group_index -> action_index (0-2)

        self._region_countdown_active = False
        self._region_countdown_end = 0.0
        self._region_countdown_group_idx = -1
        self._region_countdown_type = ""

        self._last_lines: list[str] = []
        self._term_w = 80
        self._term_h = 25

    def run(self):
        import atexit
        atexit.register(show_cursor)
        hide_cursor()
        clear_screen()

        try:
            self.monitor.start()
        except Exception as e:
            self.monitor.stats["status"] = f"启动失败: {e}"

        try:
            while self.running:
                self._update_term_size()
                self._render()

                if self._region_countdown_active and time.time() >= self._region_countdown_end:
                    self._do_region_select()

                if msvcrt.kbhit():
                    if self.reply_editing:
                        ch = msvcrt.getwch()
                        self._handle_reply_edit_char(ch)
                    else:
                        key = get_key()
                        self._handle_key(key)
                else:
                    time.sleep(0.016)
        except KeyboardInterrupt:
            pass
        finally:
            self.monitor.stop()
            show_cursor()
            clear_screen()

    def _update_term_size(self):
        try:
            sz = os.get_terminal_size()
            self._term_w = sz.columns
            self._term_h = sz.lines
        except OSError:
            pass

    def _render(self):
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
    #  Line builders
    # ═══════════════════════════════════════════════════════════

    def _line(self, content: str, W: int) -> str:
        """Normal line: │  content  │"""
        inner = W - 6
        return f"{C.GRAY}{V}  {pad_to(content, inner)}  {C.GRAY}{V}{C.RESET}"

    def _line_sel(self, content: str, W: int) -> str:
        """Selected line: │› content  │"""
        inner = W - 6
        return f"{C.GRAY}{V}{C.BOLD}› {pad_to(content, inner)}  {C.GRAY}{V}{C.RESET}"

    def _divider(self, title: str, W: int) -> str:
        """Divider: │ ──── title ──── │"""
        inner = W - 4  # │ + space + content + space + │
        if title:
            t = f"──── {title} ────"
            tw = get_display_width(t)
            fill = max(0, inner - tw)
            line = f"{C.GRAY}{t}{H * fill}{C.RESET}"
        else:
            line = f"{C.GRAY}{H * inner}{C.RESET}"
        vis = get_display_width(line)
        pad = max(0, inner - vis)
        return f"{C.GRAY}{V} {line}{' ' * pad} {C.GRAY}{V}{C.RESET}"

    def _top_border(self, title: str, W: int) -> str:
        p = f"─── {C.MAUVE}{C.BOLD}{title}{C.RESET}{C.GRAY} "
        pv = 4 + len(title) + 1
        return f"{C.GRAY}{TL}{p}{H * max(0, W - pv - 2)}{TR}{C.RESET}"

    def _bottom_border(self, W: int) -> str:
        return f"{C.GRAY}{BL}{H * (W - 2)}{BR}{C.RESET}"

    # ═══════════════════════════════════════════════════════════
    #  Main Panel
    # ═══════════════════════════════════════════════════════════

    def _render_main(self) -> list[str]:
        s = self.monitor.stats
        groups = self.config.get_groups()
        n_groups = len(groups)

        # ── Build content list to measure width ──
        items = []  # (content_str, is_selectable)

        items.append((f"{C.DIM}最后触发{C.RESET}     {C.WHITE}{s['last_trigger']}{C.RESET}", False))
        items.append((f"{C.DIM}最后回复{C.RESET}     {C.WHITE}{s['last_reply']}{C.RESET}", False))

        group_selectables = []
        self._group_opts = ["选择消息位置", "选择输入位置", "删除"]
        group_render_data = []  # (name_str, opt_str); opt_str empty for non-focused
        for i, group in enumerate(groups):
            name_str = f"{C.WHITE}{group['name']}{C.RESET}"
            is_focused = (i == self.main_selected)
            if is_focused and self._region_countdown_active:
                remaining = max(0, self._region_countdown_end - time.time())
                count = min(3, int(remaining) + 1)
                opt_str = f"{C.YELLOW}{C.BOLD}{count} 秒后截取{C.RESET}"
            elif is_focused:
                act_idx = self._group_action_indices.get(i, 0)
                parts = []
                for j, opt in enumerate(self._group_opts):
                    if j == act_idx:
                        parts.append(f"{C.SUBTEXT}[{opt}]{C.RESET}")
                    else:
                        parts.append(f"{C.SUBTEXT} {opt} {C.RESET}")
                opt_str = "".join(parts)
            else:
                opt_str = ""
            group_render_data.append((name_str, opt_str))
            items.append((name_str, True))
            group_selectables.append(4 + i)
        # 添加群 (selectable n_groups)
        items.append((f"{C.WHITE}添加群{C.RESET}", True))

        # Reply settings (selectable n_groups+1 ~ n_groups+5)
        reply_items = [
            ("检测内容",     self.config.get_reply("trigger", "@所有人"),    "str"),
            ("回复内容",     self.config.get_reply("content", ""),         "str"),
            ("最小延迟 (秒)", str(self.config.get_reply("delay_min", 1.0)), "float"),
            ("最大延迟 (秒)", str(self.config.get_reply("delay_max", 3.0)), "float"),
            ("扫描间隔 (秒)", str(self.config.get_reply("scan_interval", 2.0)), "float"),
        ]
        for i, (label, value, _) in enumerate(reply_items):
            sel = (n_groups + 1 + i) == self.main_selected
            editing = sel and self.reply_editing
            dv = self._edit_buffer if editing else value
            if editing:
                items.append((f"{C.WHITE}{label}:{C.RESET} {C.YELLOW}{dv}_{C.RESET}", True))
            elif sel:
                items.append((f"{C.WHITE}{label}:{C.RESET} {C.GREEN}{value}{C.RESET}", True))
            else:
                items.append((f"{C.WHITE}{label}:{C.RESET} {C.WHITE}{value}{C.RESET}", True))

        # ── Calculate W ──
        max_w = max(get_display_width(c) for c, _ in items)
        max_w = max(max_w, get_display_width("回复设置") + 10)
        max_w = max(max_w, get_display_width("群设置") + 10)
        # Ensure width accommodates focused group's button group (1 space between name and buttons)
        button_group_w = sum(get_display_width(opt) + 2 for opt in self._group_opts)
        max_name_w = max((get_display_width(g['name']) for g in groups), default=0)
        max_w = max(max_w, max_name_w + button_group_w)
        W = max_w + 6

        # ── Render ──
        lines = []
        lines.append(self._top_border("AutoReplyer.Kairl", W))

        # Stats (not selectable)
        for c, _ in items[:2]:
            lines.append(self._line(c, W))

        # Group settings
        lines.append(self._divider("群设置", W))
        inner = W - 6
        for i in range(n_groups):
            name_str, opt_str = group_render_data[i]
            if opt_str:
                filler = max(1, inner + 1 - get_display_width(name_str) - get_display_width(opt_str))
                content = f"{name_str}{' ' * filler}{opt_str}"
            else:
                content = name_str
            content = pad_to(content, inner + 1)
            sel = i == self.main_selected
            if sel:
                lines.append(f"{C.GRAY}{V}{C.BOLD}› {content} {C.GRAY}{V}{C.RESET}")
            else:
                lines.append(f"{C.GRAY}{V}  {content} {C.GRAY}{V}{C.RESET}")

        # Add group
        c, _ = items[2 + n_groups]
        sel = self.main_selected == n_groups
        if sel:
            inner = W - 6
            lines.append(f"{C.GRAY}{V}{C.BOLD}+ {pad_to(c, inner)}  {C.GRAY}{V}{C.RESET}")
        else:
            lines.append(self._line(c, W))

        # Reply settings
        lines.append(self._divider("回复设置", W))
        for i in range(5):
            c, _ = items[3 + n_groups + i]
            sel = (n_groups + 1 + i) == self.main_selected
            lines.append(self._line_sel(c, W) if sel else self._line(c, W))

        # Spacer + exit
        lines.append(self._line("", W))
        exit_idx = n_groups + 6
        exit_sel = self.main_selected == exit_idx
        et = "退出"
        ew = get_display_width(et)
        inner = W - 6
        sp = max(0, inner - ew)
        if exit_sel:
            lsp = max(0, sp - 2)
            el = f"{C.GRAY}{V}  {' ' * lsp}{C.BOLD}› {C.WHITE}{et}{C.RESET}  {C.GRAY}{V}{C.RESET}"
            lines.append(el)
        else:
            el = f"{C.GRAY}{V}  {' ' * sp}{C.WHITE}{et}{C.RESET}  {C.GRAY}{V}{C.RESET}"
            lines.append(el)

        lines.append(self._bottom_border(W))
        return lines

    # ═══════════════════════════════════════════════════════════
    #  Key Handling
    # ═══════════════════════════════════════════════════════════

    def _handle_key(self, key: bytes):
        if self._region_countdown_active:
            if key == K.ESC:
                self._cancel_region_countdown()
            return
        self._handle_main(key)

    def _handle_main(self, key: bytes):
        groups = self.config.get_groups()
        n_groups = len(groups)
        n = n_groups + 7  # groups + add + 5 reply + exit

        # ── Group row: Left/Right switches action, Enter executes ──
        if 0 <= self.main_selected < n_groups:
            gi = self.main_selected
            act_idx = self._group_action_indices.get(gi, 0)
            if key == K.LEFT:
                self._group_action_indices[gi] = (act_idx - 1) % 3
                return
            elif key == K.RIGHT:
                self._group_action_indices[gi] = (act_idx + 1) % 3
                return
            elif key == K.ENTER:
                self._execute_group_action(gi, act_idx)
                return

        if key == K.UP:
            self.main_selected = (self.main_selected - 1) % n
        elif key == K.DOWN:
            self.main_selected = (self.main_selected + 1) % n
        elif key == K.ENTER:
            if n_groups + 1 <= self.main_selected < n_groups + 6:
                self.reply_editing = True
                self._start_reply_edit()
            elif self.main_selected == n_groups:
                self._add_group()
                new_n = len(self.config.get_groups()) + 7
                self.main_selected = min(self.main_selected, new_n - 1)
            else:
                self.running = False
        elif key == K.ESC or key == b"\x08":
            self.running = False

    def _execute_group_action(self, gi: int, act_idx: int):
        groups = self.config.get_groups()
        if gi >= len(groups):
            return

        if act_idx == 0:
            self._start_region_countdown(gi, "message_region")
        elif act_idx == 1:
            self._start_region_countdown(gi, "reply_region")
        elif act_idx == 2:
            self.config.remove_group(gi)
            new_indices = {}
            for k, v in self._group_action_indices.items():
                if k < gi:
                    new_indices[k] = v
                elif k > gi:
                    new_indices[k - 1] = v
            self._group_action_indices = new_indices
            n = len(self.config.get_groups()) + 7
            self.main_selected = min(self.main_selected, max(0, n - 1))

    # ── Reply editing ──

    def _start_reply_edit(self):
        self._edit_buffer = ""
        self._edit_cursor = 0
        keys = ["trigger", "content", "delay_min", "delay_max", "scan_interval"]
        ri = self.main_selected - (len(self.config.get_groups()) + 1)
        if 0 <= ri < len(keys):
            val = self.config.get_reply(keys[ri], "")
            self._edit_buffer = str(val)
            self._edit_cursor = len(self._edit_buffer)

    def _handle_reply_edit_char(self, ch: str):
        if ch == "\r":
            self._commit_reply_edit()
        elif ch == "\x1b":
            self.reply_editing = False
        elif ch in ("\x00", "\xe0"):
            if msvcrt.kbhit():
                msvcrt.getwch()  # consume secondary scan code only if present
        elif ch == "\x08":
            if self._edit_cursor > 0:
                self._edit_buffer = self._edit_buffer[:self._edit_cursor-1] + self._edit_buffer[self._edit_cursor:]
                self._edit_cursor -= 1
        elif ch.isprintable():
            self._edit_buffer = self._edit_buffer[:self._edit_cursor] + ch + self._edit_buffer[self._edit_cursor:]
            self._edit_cursor += 1

    def _commit_reply_edit(self):
        keys = ["trigger", "content", "delay_min", "delay_max", "scan_interval"]
        ri = self.main_selected - (len(self.config.get_groups()) + 1)
        if ri < 0 or ri >= len(keys):
            return
        key = keys[ri]
        val = self._edit_buffer.strip()
        if key in ("trigger", "content"):
            self.config.set_reply(key, val)
        elif key in ("delay_min", "delay_max"):
            try:
                num = float(val)
                if num < 0:
                    self.reply_editing = False
                    return
                other_key = "delay_max" if key == "delay_min" else "delay_min"
                other = float(self.config.get_reply(other_key, 0.0) or 0.0)
                if num == 0 and other == 0:
                    self.config.set_reply(key, num)
                elif key == "delay_min" and num > other:
                    return
                elif key == "delay_max" and num < other:
                    return
                else:
                    self.config.set_reply(key, num)
            except ValueError:
                pass
        else:
            try:
                num = float(val)
                if num > 0:
                    self.config.set_reply(key, num)
            except ValueError:
                pass
        self.reply_editing = False

    # ── Add group ──

    def _add_group(self):
        existing = self.config.get_groups()
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

    # ── Region selector ──

    def _start_region_countdown(self, gi: int, region_type: str):
        self._region_countdown_active = True
        self._region_countdown_group_idx = gi
        self._region_countdown_type = region_type
        self._region_countdown_end = time.time() + 3.0

    def _cancel_region_countdown(self):
        self._region_countdown_active = False
        self._region_countdown_group_idx = -1
        self._region_countdown_type = ""

    def _do_region_select(self):
        gi = self._region_countdown_group_idx
        region_type = self._region_countdown_type

        self._region_countdown_active = False
        self._last_lines = []

        groups = self.config.get_groups()
        if gi < 0 or gi >= len(groups):
            return

        group = groups[gi]
        type_name = "消息区域" if region_type == "message_region" else "回复输入区域"
        title = f"框选 [{group['name']}] 的{type_name}"

        show_cursor()
        rect = self.selector.select(title=title)
        hide_cursor()

        if rect:
            self.config.set_group_region(gi, region_type, rect)

        self._last_lines = []
