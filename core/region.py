"""
Screen region selector: PySide6 QDialog overlay for drag-to-select.
Replaces the old tkinter implementation to fix PhotoImage GC issues.
Modeled after MusicClassifier's ScreenshotOverlay.
"""

import ctypes
import pyautogui
import numpy as np


# ── Win32 helpers ──

def _set_dpi_aware():
    """Make this process DPI-aware so geometry uses physical pixels.
    (Keep for pyautogui screenshot coordinate consistency.)"""
    try:
        PROCESS_PER_MONITOR_DPI_AWARE_V2 = 2
        if hasattr(ctypes.windll.shcore, "SetProcessDpiAwareness"):
            h = ctypes.windll.shcore.SetProcessDpiAwareness(
                PROCESS_PER_MONITOR_DPI_AWARE_V2
            )
            if h == 0:
                return
    except Exception:
        pass
    try:
        if hasattr(ctypes.windll.user32, "SetProcessDPIAware"):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _virtual_screen_rect():
    """Return (x, y, w, h) of the full virtual screen in physical pixels."""
    try:
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, None, None


class RegionSelector:
    """Full-screen screenshot overlay for selecting screen regions.
    Uses PySide6 (QDialog) instead of tkinter for reliability."""

    def select(self, title: str = "拖拽选择区域") -> dict | None:
        """Show full-screen overlay, let user drag-select a region.
        Returns {"x","y","w","h"} in physical pixels, or None on cancel."""
        from PySide6.QtWidgets import (
            QDialog, QApplication, QVBoxLayout, QHBoxLayout,
            QPushButton, QLabel, QWidget,
        )
        from PySide6.QtCore import Qt, QPoint, QRect
        from PySide6.QtGui import (
            QPainter, QColor, QPen, QImage, QPixmap, QPainterPath,
        )

        _set_dpi_aware()

        # Ensure a QApplication exists (TUI runs without one).
        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        vx, vy, vw, vh = _virtual_screen_rect()
        if vw is None:
            screen = app.primaryScreen()
            if screen:
                geo = screen.geometry()
                vx, vy, vw, vh = geo.x(), geo.y(), geo.width(), geo.height()
            else:
                vx, vy, vw, vh = 0, 0, 1920, 1080

        screenshot = pyautogui.screenshot(region=(vx, vy, vw, vh))
        img_array = np.array(screenshot.convert("RGB"))
        h, w = img_array.shape[:2]

        dpr = app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 1.0

        result = {"rect": None}

        class _Overlay(QDialog):
            def __init__(self):
                super().__init__()
                self._selecting = False
                self._start_point: QPoint | None = None
                self._end_point: QPoint | None = None
                self._confirmed_rect: QRect | None = None
                self._dpr = dpr
                self._background: QPixmap | None = None
                self._init_ui()
                self._init_background(img_array, w, h)

            def _init_ui(self):
                self.setWindowFlags(
                    Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog
                )
                self.setAttribute(Qt.WA_TranslucentBackground)
                self.setCursor(Qt.CrossCursor)
                self.setFocusPolicy(Qt.StrongFocus)
                self.setGeometry(vx, vy, vw, vh)

                # Toolbar
                self._toolbar = QWidget(self)
                self._toolbar.setVisible(False)
                self._toolbar.setStyleSheet(
                    "background-color: #ffffff; border: none; border-radius: 12px; padding: 4px;"
                )
                tlayout = QHBoxLayout(self._toolbar)
                tlayout.setContentsMargins(8, 4, 8, 4)
                tlayout.setSpacing(8)

                self._size_label = QLabel("")
                self._size_label.setStyleSheet(
                    "color: #202124; font-size: 12px; font-weight: 500;"
                )
                tlayout.addWidget(self._size_label)

                confirm_btn = QPushButton("确认")
                confirm_btn.setStyleSheet(
                    "background-color: #5f6368; color: #ffffff; border: none; "
                    "border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 600;"
                )
                confirm_btn.clicked.connect(self._on_confirm)
                tlayout.addWidget(confirm_btn)

                cancel_btn = QPushButton("取消")
                cancel_btn.setStyleSheet(
                    "background-color: #e8eaed; color: #202124; border: none; "
                    "border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 500;"
                )
                cancel_btn.clicked.connect(self._on_cancel)
                tlayout.addWidget(cancel_btn)

            def _init_background(self, rgb, w, h):
                qimg = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888)
                self._background = QPixmap.fromImage(qimg.copy())

            def paintEvent(self, event):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.Antialiasing)

                if self._background:
                    painter.drawPixmap(self.rect(), self._background)

                overlay = QColor(0, 0, 0, 140)
                if self._start_point and self._end_point:
                    rect = QRect(self._start_point, self._end_point).normalized()
                    path = QPainterPath()
                    path.addRect(self.rect())
                    hole = QPainterPath()
                    hole.addRect(rect)
                    path = path.subtracted(hole)
                    painter.fillPath(path, overlay)

                    painter.setPen(QPen(QColor(255, 255, 255), 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect)

                    # Crosshair
                    painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
                    self._draw_crosshair(painter, rect)
                else:
                    painter.fillRect(self.rect(), overlay)

                painter.end()

            def _draw_crosshair(self, painter, rect):
                cx = rect.center().x()
                cy = rect.center().y()
                dash_len, gap_len = 6, 4

                def _dashed(x1, y1, x2, y2):
                    dx, dy = x2 - x1, y2 - y1
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist == 0:
                        return
                    ux, uy = dx / dist, dy / dist
                    pos, draw = 0.0, True
                    while pos < dist:
                        seg = dash_len if draw else gap_len
                        seg = min(seg, dist - pos)
                        sx, sy = x1 + ux * pos, y1 + uy * pos
                        ex, ey = x1 + ux * (pos + seg), y1 + uy * (pos + seg)
                        if draw:
                            painter.drawLine(int(sx), int(sy), int(ex), int(ey))
                        pos += seg
                        draw = not draw

                _dashed(rect.left(), cy, rect.left() - 20, cy)
                _dashed(rect.right(), cy, rect.right() + 20, cy)
                _dashed(cx, rect.top(), cx, rect.top() - 20)
                _dashed(cx, rect.bottom(), cx, rect.bottom() + 20)

            def mousePressEvent(self, event):
                if event.button() == Qt.LeftButton:
                    self._selecting = True
                    self._start_point = QPoint(self.width() // 2, self.height() // 2)
                    self._end_point = event.pos()
                    self._toolbar.setVisible(False)
                    self.update()

            def mouseMoveEvent(self, event):
                if self._selecting:
                    self._end_point = event.pos()
                    self.update()

            def mouseReleaseEvent(self, event):
                if event.button() == Qt.LeftButton and self._selecting:
                    self._selecting = False
                    self._end_point = event.pos()
                    rect = QRect(self._start_point, self._end_point).normalized()
                    if rect.width() > 5 and rect.height() > 5:
                        self._confirmed_rect = rect
                        self._size_label.setText(
                            f"{int(rect.width() * self._dpr)} x {int(rect.height() * self._dpr)}"
                        )
                        self._position_toolbar(rect)
                        self._toolbar.setVisible(True)
                    self.update()

            def _position_toolbar(self, rect):
                tw = self._toolbar.sizeHint().width()
                th = self._toolbar.sizeHint().height()
                x = rect.center().x() - tw // 2
                y = rect.bottom() + 8
                if y + th > self.height():
                    y = rect.top() - th - 8
                x = max(0, min(x, self.width() - tw))
                self._toolbar.move(x, y)

            def _on_confirm(self):
                if self._confirmed_rect:
                    r = self._confirmed_rect
                    result["rect"] = {
                        "x": int(r.x() * self._dpr) + vx,
                        "y": int(r.y() * self._dpr) + vy,
                        "w": int(r.width() * self._dpr),
                        "h": int(r.height() * self._dpr),
                    }
                self.close()

            def _on_cancel(self):
                self.close()

            def keyPressEvent(self, event):
                if event.key() == Qt.Key_Escape:
                    self._on_cancel()
                elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._on_confirm()

        overlay = _Overlay()
        overlay.setWindowTitle(title)
        overlay.show()
        overlay.activateWindow()
        overlay.setFocus(Qt.OtherFocusReason)

        app.exec()

        return result["rect"]
