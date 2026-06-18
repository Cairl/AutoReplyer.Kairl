"""
Terminal UI utilities: colors, display helpers, keyboard input.
"""

import os
import sys
import re as _re
import time
import unicodedata
import msvcrt

os.system("")  # Enable VT100 on Windows


# ═══════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════

ESC = chr(27)


class C:
    RESET       = f"{ESC}[0m"
    BOLD        = f"{ESC}[97;1m"
    DIM         = f"{ESC}[2m"
    RED         = f"{ESC}[38;2;243;139;168m"
    GREEN       = f"{ESC}[38;2;166;227;161m"
    YELLOW      = f"{ESC}[38;2;249;226;175m"
    BLUE        = f"{ESC}[38;2;137;180;250m"
    MAUVE       = f"{ESC}[38;2;203;166;247m"
    TEAL        = f"{ESC}[38;2;148;226;213m"
    GRAY        = f"{ESC}[38;2;148;152;178m"
    SUBTEXT     = f"{ESC}[38;2;186;194;222m"
    WHITE       = f"{ESC}[38;2;205;214;244m"
    BG_SELECT   = f"{ESC}[48;2;49;116;143m"
    BG_PANEL    = f"{ESC}[48;2;30;30;46m"
    STRIKE      = f"{ESC}[9m"


TL, TR, BL, BR = "╭", "╮", "╰", "╯"
H, V = "─", "│"


class K:
    UP    = b"H"
    DOWN  = b"P"
    LEFT  = b"K"
    RIGHT = b"M"
    ENTER = b"\r"
    ESC   = b"\x1b"


# ═══════════════════════════════════════════════════════════════
#  Display Helpers
# ═══════════════════════════════════════════════════════════════

_ANSI_RE = _re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def get_display_width(text: str) -> int:
    clean = strip_ansi(text)
    w = 0
    for ch in clean:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("F", "W") else 1
    return w


def pad_to(text: str, target_w: int) -> str:
    """Pad text with spaces to reach target display width."""
    vis = get_display_width(text)
    return text + " " * max(0, target_w - vis)


# ═══════════════════════════════════════════════════════════════
#  Terminal Control
# ═══════════════════════════════════════════════════════════════

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
    key = msvcrt.getch()
    if key in (b"\xe0", b"\x00"):
        return msvcrt.getch()
    return key


def drain_keyboard():
    while msvcrt.kbhit():
        msvcrt.getch()
