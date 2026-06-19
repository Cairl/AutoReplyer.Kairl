"""
Tests for region.py (PySide6 QDialog implementation).

Verifies:
  - _set_dpi_aware() and _virtual_screen_rect() exist and are callable
  - RegionSelector class exists with select() method
  - select() uses PySide6 patterns: QApplication, QDialog, FramelessWindowHint
  - Screenshot region uses vx, vy, vw, vh
  - DPI handling via devicePixelRatio()
  - Result rect uses physical pixels (dpr scaling + vx/vy offset)
  - Escape cancels, Enter/Return confirms

All tests use source-code inspection against the raw region.py file text
so we do NOT trigger pyautogui/numpy/PySide6 imports.
"""

import os
import re
import sys
import unittest
import inspect

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Read region.py source as raw text for source-level assertions.
REGION_PATH = os.path.join(PROJECT_ROOT, "core", "region.py")
with open(REGION_PATH, "r", encoding="utf-8") as f:
    _REGION_SOURCE = f.read()

# Stub pyautogui/numpy so we can import region module for inspect.getsource.
_mock_pyautogui = type(sys)("pyautogui")
_mock_pyautogui.screenshot = lambda *a, **kw: None
sys.modules["pyautogui"] = _mock_pyautogui
sys.modules["numpy"] = type(sys)("numpy")

from core.region import (
    _set_dpi_aware,
    _virtual_screen_rect,
    RegionSelector,
)


class TestSetDpiAware(unittest.TestCase):
    """_set_dpi_aware() — unchanged from tkinter version."""

    def test_function_exists_and_is_callable(self):
        self.assertTrue(callable(_set_dpi_aware))

    def test_function_does_not_raise(self):
        from unittest.mock import patch
        with patch("ctypes.windll.shcore.SetProcessDpiAwareness",
                   side_effect=OSError("mock fail")):
            with patch("ctypes.windll.user32.SetProcessDPIAware",
                       side_effect=OSError("mock fail")):
                try:
                    _set_dpi_aware()
                except Exception as e:
                    self.fail(f"_set_dpi_aware raised unexpectedly: {e}")

    def test_tries_per_monitor_v2_first(self):
        source = inspect.getsource(_set_dpi_aware)
        try_positions = [i for i in range(len(source))
                         if source.startswith("try:", i)]
        self.assertGreaterEqual(len(try_positions), 2,
                                "Expected at least 2 try blocks")
        first_try = try_positions[0]
        second_try = try_positions[1]
        first_body = source[first_try:second_try]
        self.assertIn("SetProcessDpiAwareness", first_body,
                      "First try block must use SetProcessDpiAwareness (V2)")
        second_body = source[second_try:]
        self.assertIn("SetProcessDPIAware", second_body,
                      "Second try block must use SetProcessDPIAware (legacy)")


class TestVirtualScreenRect(unittest.TestCase):
    """_virtual_screen_rect() — unchanged from tkinter version."""

    def test_function_exists_and_is_callable(self):
        self.assertTrue(callable(_virtual_screen_rect))

    def test_returns_4_tuple(self):
        result = _virtual_screen_rect()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 4)

    def test_uses_correct_system_metrics(self):
        source = inspect.getsource(_virtual_screen_rect)
        self.assertIn("SM_XVIRTUALSCREEN", source)
        self.assertIn("SM_YVIRTUALSCREEN", source)
        self.assertIn("SM_CXVIRTUALSCREEN", source)
        self.assertIn("SM_CYVIRTUALSCREEN", source)

    def test_fallback_when_win32_fails(self):
        from unittest.mock import patch
        with patch("ctypes.windll.user32.GetSystemMetrics",
                   side_effect=OSError("mock fail")):
            result = _virtual_screen_rect()
            self.assertEqual(result, (0, 0, None, None))


class TestRegionSelectorSelectMethod(unittest.TestCase):
    """select() uses PySide6 QDialog patterns."""

    def setUp(self):
        self.selector = RegionSelector()

    def test_select_calls_set_dpi_aware(self):
        """select() must call _set_dpi_aware() before creating QApplication."""
        source = inspect.getsource(RegionSelector.select)
        idx_dpi = source.find("_set_dpi_aware()")
        idx_app = source.find("QApplication([]")
        self.assertGreater(idx_dpi, -1, "_set_dpi_aware() call not found in select()")
        self.assertGreater(idx_app, -1, "QApplication([]) not found in select()")
        self.assertLess(idx_dpi, idx_app,
                        "_set_dpi_aware() must be called before QApplication is created")

    def test_uses_qapplication_instance_check(self):
        """select() checks QApplication.instance() before creating new one."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("QApplication.instance()", source,
                      "Must check for existing QApplication instance")
        self.assertIn("QApplication([])", source,
                      "Must create QApplication if none exists")

    def test_uses_qdialog_with_correct_flags(self):
        """_Overlay uses QDialog with FramelessWindowHint | WindowStaysOnTopHint."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("FramelessWindowHint", source,
                      "Must use FramelessWindowHint (replaces overrideredirect)")
        self.assertIn("WindowStaysOnTopHint", source,
                      "Must use WindowStaysOnTopHint (replaces -topmost)")

    def test_uses_qpainterpath_for_dim_overlay(self):
        """Dark overlay uses QPainterPath subtraction (not stipple)."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("QPainterPath", source,
                      "Must use QPainterPath instead of tkinter stipple")
        self.assertIn("subtracted", source,
                      "Must use path.subtracted() for the selection cutout")

    def test_screenshot_uses_virtual_screen_coords(self):
        """Screenshot region must use (vx, vy, vw, vh), not None."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("pyautogui.screenshot(region=(vx, vy, vw, vh)", source,
                      "Screenshot must use virtual screen coordinates")

    def test_fallback_when_vw_is_none(self):
        """When vw is None, falls back to screen.geometry()."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("vw is None", source)
        self.assertIn("screen.geometry()", source)

    def test_geometry_uses_setgeometry_with_virtual_screen(self):
        """QDialog uses setGeometry(vx, vy, vw, vh)."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("setGeometry(vx, vy, vw, vh)", source,
                      "setGeometry must use virtual screen coords")


class TestDpiHandling(unittest.TestCase):
    """DPI handling via devicePixelRatio()."""

    def test_device_pixel_ratio_used(self):
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("devicePixelRatio", source,
                      "Must use devicePixelRatio() for DPI scaling")

    def test_dpr_multiplies_rect_coords(self):
        """Result rect coordinates multiplied by dpr and offset by vx/vy."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn('"x": int(r.x() * self._dpr) + vx', source,
                      "x must be scaled: r.x() * dpr + vx")
        self.assertIn('"y": int(r.y() * self._dpr) + vy', source,
                      "y must be scaled: r.y() * dpr + vy")
        self.assertIn('"w": int(r.width() * self._dpr)', source,
                      "w must be scaled: r.width() * dpr")
        self.assertIn('"h": int(r.height() * self._dpr)', source,
                      "h must be scaled: r.height() * dpr")


class TestKeyboardAndCancel(unittest.TestCase):
    """Escape cancels, Enter/Return confirms."""

    def test_escape_key_cancels(self):
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("Qt.Key_Escape", source,
                      "Must handle Escape key")
        self.assertIn("_on_cancel", source,
                      "Escape must call _on_cancel")

    def test_enter_key_confirms(self):
        source = inspect.getsource(RegionSelector.select)
        self.assertIn("Qt.Key_Return", source,
                      "Must handle Return key")
        self.assertIn("Qt.Key_Enter", source,
                      "Must handle Enter key (numpad)")
        self.assertIn("_on_confirm", source,
                      "Enter/Return must call _on_confirm")

    def test_toolbar_exists_with_confirm_cancel(self):
        """Toolbar has confirm and cancel QPushButtons."""
        source = _REGION_SOURCE
        self.assertIn("确认", source, "Toolbar must have confirm button")
        self.assertIn("取消", source, "Toolbar must have cancel button")

    def test_size_label_shows_physical_pixels(self):
        """Size label text uses dpr-scaled dimensions."""
        source = _REGION_SOURCE
        self.assertIn("self._dpr", source,
                      "Size label must use dpr for physical pixel display")


class TestResultRectContract(unittest.TestCase):
    """The returned dict contract: {"x","y","w","h"} in physical pixels or None."""

    def setUp(self):
        self.selector = RegionSelector()

    def test_select_returns_dict_or_none(self):
        """select() returns dict or None."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn('"x":', source, "Must return dict with 'x' key")
        self.assertIn('"y":', source, "Must return dict with 'y' key")
        self.assertIn('"w":', source, "Must return dict with 'w' key")
        self.assertIn('"h":', source, "Must return dict with 'h' key")
        self.assertIn('return result["rect"]', source,
                      "Must return result['rect'] (None if cancelled)")

    def test_result_uses_result_dot_rect(self):
        """Cancellation returns None via result["rect"] pattern."""
        source = inspect.getsource(RegionSelector.select)
        self.assertIn('result["rect"]', source,
                      "Must use result['rect'] pattern for cancellation")


if __name__ == "__main__":
    unittest.main()
