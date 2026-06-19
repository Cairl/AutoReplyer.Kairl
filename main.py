"""
AutoReplyer.Kairl
WeChat Group @all Auto-Reply Tool
Console TUI with OCR-based screen monitoring
"""

# Mark the process DPI-aware BEFORE any GUI/tkinter import, so every window
# (region selector, overlay) uses physical-pixel geometry that matches
# pyautogui screenshots and the stored regions. Critical on scaled/multi-monitor.
from core.overlay import _set_dpi_aware
_set_dpi_aware()

import pyautogui

from ui.tui import TUI


def main():
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05

    app = TUI()
    app.run()


if __name__ == "__main__":
    main()
