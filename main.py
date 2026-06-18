"""
AutoReplyer.Kairl
WeChat Group @all Auto-Reply Tool
Console TUI with OCR-based screen monitoring
"""

import pyautogui

from ui.tui import TUI


def main():
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05

    app = TUI()
    app.run()


if __name__ == "__main__":
    main()
