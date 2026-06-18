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

        self.group_modal_active = False
        self.group_modal_idx = 0
        self._ga_group_idx = -1

        self._region_wait_active = False
        self._region_wait_msg = ""
        self._region_wait_type = ""
        self._region_wait_sel = 0

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

                if msvcrt.kbhit():
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
    #  Line builders
    # ═══════════════════════════════════════════════════════════

    def _line(self, content: str, W: int) -> str:
        """Normal line: │  content  │"""
        inner = W - 6
        return f"{C.GRAY}{V}  {pad_to(content, inner)}  {C.GRAY}{V}{C.RESET}"

    def _line_sel(self, content: str, W: int) -> str:
        """Selected line: │› content  │"""
        inner = W - 6
        return f"{C.GRAY}{V}{C.BOLD}› {pad_to(content, inner - 1)}  {C.GRAY}{V}{C.RESET}"

    def _divider(self, title: str, W: int) -> str:
        """Divider: │  ─── title ─────  │"""
        inner = W - 6
        if title:
            t = f"─── {title} ───"
            tw = 3 + 1 + get_display_width(title) + 1 + 3
            line = f"{C.GRAY}{t}{H * max(0, inner - tw)}{C.RESET}"
        else:
            line = f"{C.GRAY}{H * inner}{C.RESET}"
        return f"{C.GRAY}{V}  {pad_to(line, inner)}  {C.GRAY}{V}{C.RESET}"

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

        # ── Build content list to measure width ──
        items = []  # (content_str, is_selectable)

        items.append((
            f"{C.DIM}触发:{C.RESET} {C.YELLOW}{s['triggers']}{C.RESET}       "
            f"{C.DIM}回复:{C.RESET} {C.GREEN}{s['replies']}{C.RESET}       "
            f"{C.DIM}错误:{C.RESET} {C.RED}{s['errors']}{C.RESET}", False))
        items.append((f"{C.DIM}最后触发{C.RESET}     {C.WHITE}{s['last_trigger']}{C.RESET}", False))
        items.append((f"{C.DIM}最后回复{C.RESET}     {C.WHITE}{s['last_reply']}{C.RESET}", False))

        reply_items = [
            ("回复内容",     self.config.get_reply("content", ""),         "str"),
            ("最小延迟 (秒)", str(self.config.get_reply("delay_min", 1.0)), "float"),
            ("最大延迟 (秒)", str(self.config.get_reply("delay_max", 3.0)), "float"),
            ("扫描间隔 (秒)", str(self.config.get_reply("scan_interval", 2.0)), "float"),
        ]
        for i, (label, value, _) in enumerate(reply_items):
            sel = i == self.main_selected
            editing = sel and self.reply_editing
            dv = self._edit_buffer if editing else value
            if editing:
                items.append((f"{C.WHITE}{label}:{C.RESET} {C.YELLOW}{dv}_{C.RESET}", True))
            elif sel:
                items.append((f"{C.WHITE}{label}:{C.RESET} {C.GREEN}{value}{C.RESET}", True))
            else:
                items.append((f"{C.WHITE}{label}:{C.RESET} {C.WHITE}{value}{C.RESET}", True))

        group_selectables = []
        for i, group in enumerate(groups):
            enabled = group.get("enabled", True)
            has_msg = group.get("message_region") is not None
            has_rpl = group.get("reply_region") is not None
            sp = []
            sp.append(f"{C.GREEN}启用{C.RESET}" if enabled else f"{C.RED}停用{C.RESET}")
            sp.append(f"{C.GREEN}消息{C.RESET}" if has_msg else f"{C.DIM}----{C.RESET}")
            sp.append(f"{C.GREEN}回复{C.RESET}" if has_rpl else f"{C.DIM}----{C.RESET}")
            items.append((f"{C.WHITE}{group['name']}{C.RESET}  [{' '.join(sp)}]", True))
            group_selectables.append(4 + i)

            if self.group_modal_active and self._ga_group_idx == i:
                for action in ["停用监视" if enabled else "启用监视", "设置消息区域", "设置回复区域", "删除此群"]:
                    items.append((f"{C.WHITE}{action}{C.RESET}", True))

        items.append((f"{C.WHITE}+ 添加群{C.RESET}", True))

        # ── Calculate W ──
        max_w = max(get_display_width(c) for c, _ in items)
        max_w = max(max_w, get_display_width("回复设置") + 8)
        max_w = max(max_w, get_display_width("群设置") + 8)
        W = max_w + 6

        # ── Render ──
        lines = []
        lines.append(self._top_border("AutoReplyer.Kairl", W))

        # Stats (not selectable)
        for c, _ in items[:3]:
            lines.append(self._line(c, W))

        # Reply settings
        lines.append(self._divider("回复设置", W))
        for i in range(3, 7):
            c, _ = items[i]
            sel = (i - 3) == self.main_selected
            lines.append(self._line_sel(c, W) if sel else self._line(c, W))

        # Group settings
        lines.append(self._divider("群设置", W))
        if groups:
            idx = 7
            for i, group in enumerate(groups):
                c, _ = items[idx]
                sel = (4 + i) == self.main_selected
                lines.append(self._line_sel(c, W) if sel else self._line(c, W))
                idx += 1
                if self.group_modal_active and self._ga_group_idx == i:
                    for j in range(4):
                        c, _ = items[idx]
                        action_sel = j == self.group_modal_idx
                        lines.append(self._line_sel(c, W) if action_sel else self._line(c, W))
                        idx += 1

        # Add group
        add_idx = 4 + len(groups)
        c, _ = items[-1]
        sel = self.main_selected == add_idx
        lines.append(self._line_sel(c, W) if sel else self._line(c, W))

        # Divider + exit
        lines.append(self._divider("", W))
        exit_idx = add_idx + 1
        exit_sel = self.main_selected == exit_idx
        et = "退出"
        ew = get_display_width(et)
        inner = W - 6
        if exit_sel:
            lp = max(0, inner - ew - 1)
            el = f"{' ' * lp}{C.WHITE}{et}{C.RESET}"
            lines.append(self._line_sel(el, W))
        else:
            lp = max(0, inner - ew)
            el = f"{' ' * lp}{C.WHITE}{et}{C.RESET}"
            lines.append(self._line(el, W))

        lines.append(self._bottom_border(W))
        return lines

    # ═══════════════════════════════════════════════════════════
    #  Key Handling
    # ═══════════════════════════════════════════════════════════

    def _handle_key(self, key: bytes):
        if self._region_wait_active:
            self._handle_region_wait(key)
        else:
            self._handle_main(key)

    def _handle_main(self, key: bytes):
        if self.reply_editing:
            self._handle_reply_edit(key)
            return

        groups = self.config.get_groups()
        n = 4 + len(groups) + 2

        if self.group_modal_active:
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
                self.reply_editing = True
                self._start_reply_edit()
            elif self.main_selected < 4 + len(groups):
                self._ga_group_idx = self.main_selected - 4
                self.group_modal_active = True
                self.group_modal_idx = 0
            elif self.main_selected == 4 + len(groups):
                self._add_group()
                new_n = 4 + len(self.config.get_groups()) + 2
                self.main_selected = min(self.main_selected, new_n - 1)
            else:
                self.running = False
        elif key == K.ESC or key == b"\x08":
            self.running = False

    def _execute_group_modal_action(self):
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            self.group_modal_active = False
            return

        idx = self._ga_group_idx

        if self.group_modal_idx == 0:
            self.config.toggle_group(idx)
        elif self.group_modal_idx == 1:
            self._select_region("message_region")
        elif self.group_modal_idx == 2:
            self._select_region("reply_region")
        elif self.group_modal_idx == 3:
            self.config.remove_group(idx)
            n = 4 + len(self.config.get_groups()) + 2
            self.main_selected = min(self.main_selected, max(0, n - 1))

        self.group_modal_active = False

    # ── Reply editing ──

    def _start_reply_edit(self):
        self._edit_buffer = ""
        self._edit_cursor = 0
        keys = ["content", "delay_min", "delay_max", "scan_interval"]
        if self.main_selected < len(keys):
            val = self.config.get_reply(keys[self.main_selected], "")
            self._edit_buffer = str(val)
            self._edit_cursor = len(self._edit_buffer)

    def _handle_reply_edit(self, key: bytes):
        ch = msvcrt.getwch()
        if ch == "\r":
            self._commit_reply_edit()
        elif ch == "\x1b":
            self.reply_editing = False
        elif ch == "\x00":
            msvcrt.getwch()
        elif ch == "\x08":
            if self._edit_cursor > 0:
                self._edit_buffer = self._edit_buffer[:self._edit_cursor-1] + self._edit_buffer[self._edit_cursor:]
                self._edit_cursor -= 1
        elif ch.isprintable():
            self._edit_buffer = self._edit_buffer[:self._edit_cursor] + ch + self._edit_buffer[self._edit_cursor:]
            self._edit_cursor += 1

    def _commit_reply_edit(self):
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

    def _select_region(self, region_type: str):
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
        title = self._region_wait_msg
        contents = [
            f"{C.DIM}即将显示全屏截图{C.RESET}",
            f"{C.DIM}拖拽鼠标框选目标区域{C.RESET}",
            f"{C.YELLOW}按 Enter 开始框选，Esc 取消{C.RESET}",
            f"{C.WHITE}开始框选{C.RESET}",
            f"{C.WHITE}取消{C.RESET}",
            f"{C.DIM}[Up/Down] 导航    [Enter] 确认    [Esc] 返回{C.RESET}",
        ]
        max_w = max(get_display_width(c) for c in contents)
        max_w = max(max_w, get_display_width(title) + 8)
        W = max_w + 6

        lines = []
        tp = f"─── {C.BLUE}{C.BOLD}{title}{C.RESET}{C.GRAY} "
        pv = 4 + get_display_width(title) + 1
        lines.append(f"{C.GRAY}{TL}{tp}{H * max(0, W - pv - 2)}{TR}{C.RESET}")
        lines.append(self._line(contents[0], W))
        lines.append(self._line(contents[1], W))
        lines.append(self._line(contents[2], W))
        lines.append(self._divider("", W))
        lines.append(self._line_sel(contents[3], W))
        lines.append(self._line(contents[4], W))
        lines.append(self._line(contents[5], W))
        lines.append(self._bottom_border(W))
        return lines

    def _handle_region_wait(self, key: bytes):
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
        groups = self.config.get_groups()
        if self._ga_group_idx >= len(groups):
            self._region_wait_active = False
            self._last_lines = []
            return

        title = self._region_wait_msg
        show_cursor()
        rect = self.selector.select(title=title)
        hide_cursor()

        if rect:
            self.config.set_group_region(self._ga_group_idx, self._region_wait_type, rect)

        self._region_wait_active = False
        self._last_lines = []
