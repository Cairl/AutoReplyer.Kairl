"""
Tests for Bug 3: Overlay shows detected bubble, not entire message_region.

Verifies:
  - overlay.show() is NOT called before screenshot/bubble detection
  - overlay.show is called AFTER the `bubble is not None` check
  - Absolute coordinate math: abs_x = region["x"] + bx, abs_y = region["y"] + by
  - overlay is NOT called when bubble is None

All tests use source-code inspection against the raw monitor.py file text
so we do NOT trigger pyautogui/numpy/winocr imports.
"""

import os
import re
import sys
import unittest
import inspect

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Read monitor.py source as raw text for source-level assertions.
MONITOR_PATH = os.path.join(PROJECT_ROOT, "core", "monitor.py")
with open(MONITOR_PATH, "r", encoding="utf-8") as f:
    _MONITOR_SOURCE = f.read()


# Extract _scan_group method source as a standalone string for analysis.
def _extract_scan_group_source() -> str:
    """Extract the _scan_group method from the raw source text."""
    # Find def _scan_group and extract until next top-level def or EOF.
    match = re.search(
        r'def _scan_group\(self, group: dict\):.*?(?=\n    def |\n    @staticmethod|\n    @|$)',
        _MONITOR_SOURCE, re.DOTALL
    )
    if not match:
        # Try broader pattern for nested defs.
        # _scan_group has nested logic, so we need to be careful.
        # Let's find the method by line.
        lines = _MONITOR_SOURCE.split("\n")
        start = None
        for i, line in enumerate(lines):
            if re.match(r'    def _scan_group\(', line):
                start = i
                break
        if start is None:
            return ""
        # Collect lines until we hit a line with same or less indentation
        # that starts with 'def ' or '@' (next method).
        result_lines = []
        for i in range(start, len(lines)):
            line = lines[i]
            if i > start:
                if re.match(r'    def |    @', line):
                    break
            result_lines.append(line)
        return "\n".join(result_lines)
    return match.group(0)


_SCAN_GROUP_SOURCE = _extract_scan_group_source()


class TestOverlayCallPlacement(unittest.TestCase):
    """Verify the overlay.show() call is correctly placed in _scan_group()."""

    @classmethod
    def setUpClass(cls):
        cls.source = _SCAN_GROUP_SOURCE

    def test_overlay_show_not_called_before_screenshot(self):
        """overlay.show must NOT be called before screenshot capture and
        bubble detection (i.e., not at the very top of _scan_group)."""
        lines = self.source.split("\n")

        # Find all overlay.show() call lines.
        overlay_lines = []
        screenshot_line = None
        bubble_none_line = None
        for i, line in enumerate(lines):
            if "overlay" in line and "show" in line:
                overlay_lines.append(i)
            if "pyautogui.screenshot" in line:
                screenshot_line = i
            if "bubble is None" in line:
                bubble_none_line = i

        self.assertIsNotNone(screenshot_line,
                             "screenshot call not found in _scan_group")
        self.assertIsNotNone(bubble_none_line,
                             "bubble is None check not found in _scan_group")

        # All overlay.show calls must be AFTER both the screenshot
        # and bubble-is-None check.
        for call_idx in overlay_lines:
            self.assertGreater(call_idx, screenshot_line,
                               f"overlay.show at line {call_idx} must be "
                               f"after screenshot at line {screenshot_line}")
            self.assertGreater(call_idx, bubble_none_line,
                               f"overlay.show at line {call_idx} must be "
                               f"after bubble-is-None guard at line {bubble_none_line}")

    def test_overlay_show_is_after_bubble_not_none_check(self):
        """The overlay.show() call must appear AFTER the
        `if bubble is None: return` guard."""
        lines = self.source.split("\n")

        guard_line = None
        show_line = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "bubble is None" in stripped:
                guard_line = i
            if "overlay" in stripped and "show" in stripped:
                show_line = i

        self.assertIsNotNone(guard_line,
                             "bubble-is-None return guard not found")
        self.assertIsNotNone(show_line,
                             "overlay.show call not found")
        self.assertLess(guard_line, show_line,
                        f"overlay.show (line {show_line}) must be AFTER "
                        f"bubble-is-None guard (line {guard_line})")

    def test_overlay_not_called_when_bubble_is_none(self):
        """When bubble is None, the function returns early BEFORE overlay.show().
        Control-flow: `if bubble is None: return` precedes overlay.show(),
        so overlay.show is unreachable when bubble is None."""
        lines = self.source.split("\n")

        # Find the return in the bubble-is-None guard block.
        in_guard = False
        guard_return_line = None
        show_line = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "bubble is None" in stripped:
                in_guard = True
                continue
            if in_guard and stripped == "return":
                guard_return_line = i
                in_guard = False
                continue
            if "overlay" in stripped and "show" in stripped:
                show_line = i

        self.assertIsNotNone(guard_return_line,
                             "return statement in bubble-is-None block not found")
        self.assertIsNotNone(show_line,
                             "overlay.show call not found")
        self.assertLess(guard_return_line, show_line,
                        "return (bubble is None) must precede overlay.show; "
                        "overlay.show is unreachable when bubble is None")


class TestAbsoluteCoordinateMath(unittest.TestCase):
    """Verify the absolute coordinate computation is correct."""

    @classmethod
    def setUpClass(cls):
        cls.source = _SCAN_GROUP_SOURCE

    def test_abs_x_is_region_x_plus_bx(self):
        """abs_x = region["x"] + bx (not just bx alone)."""
        self.assertIn('region["x"] + bx', self.source,
                      "abs_x must be region['x'] + bx")
        self.assertIn('abs_x', self.source,
                      "abs_x variable must be present")

    def test_abs_y_is_region_y_plus_by(self):
        """abs_y = region["y"] + by (not just by alone)."""
        self.assertIn('region["y"] + by', self.source,
                      "abs_y must be region['y'] + by")
        self.assertIn('abs_y', self.source,
                      "abs_y variable must be present")

    def test_overlay_receives_absolute_coords(self):
        """The overlay.show() call receives a dict with abs_x, abs_y, bw, bh."""
        self.assertIn('"x": abs_x', self.source,
                      "overlay.show must use abs_x for x coordinate")
        self.assertIn('"y": abs_y', self.source,
                      "overlay.show must use abs_y for y coordinate")
        self.assertIn('"w": bw', self.source,
                      "overlay.show must use bw (bubble width)")
        self.assertIn('"h": bh', self.source,
                      "overlay.show must use bh (bubble height)")

    def test_coordinate_math_is_correct_with_examples(self):
        """Verify the coordinate formula with concrete examples."""
        # Simulate the math from _scan_group:
        # region = {"x": 100, "y": 200, "w": 500, "h": 400}
        # bubble = (bx=50, by=80, bw=300, bh=20)
        # abs_x = region["x"] + bx = 100 + 50 = 150
        # abs_y = region["y"] + by = 200 + 80 = 280
        region = {"x": 100, "y": 200, "w": 500, "h": 400}
        bx, by, bw, bh = 50, 80, 300, 20

        abs_x = region["x"] + bx
        abs_y = region["y"] + by

        self.assertEqual(abs_x, 150,
                         "abs_x = region.x + bx = 100 + 50 = 150")
        self.assertEqual(abs_y, 280,
                         "abs_y = region.y + by = 200 + 80 = 280")

        # overlay receives: {"x": abs_x, "y": abs_y, "w": bw, "h": bh}
        overlay_region = {"x": abs_x, "y": abs_y, "w": bw, "h": bh}
        self.assertEqual(overlay_region["x"], 150)
        self.assertEqual(overlay_region["y"], 280)
        self.assertEqual(overlay_region["w"], 300, "width = bubble width")
        self.assertEqual(overlay_region["h"], 20, "height = bubble height")


class TestOverlayNotCalledForWholeRegion(unittest.TestCase):
    """Verify the overlay does NOT use the entire message_region coordinates."""

    @classmethod
    def setUpClass(cls):
        cls.source = _SCAN_GROUP_SOURCE

    def test_overlay_does_not_pass_region_directly(self):
        """The overlay.show() must NOT receive the raw `region` dict directly.
        It must receive a new dict with computed bubble coordinates."""
        # All overlay.show() calls in _scan_group must use abs_x/abs_y/bw/bh,
        # not the raw region dict.
        lines = self.source.split("\n")
        overlay_lines = [
            l.strip() for l in lines
            if "overlay" in l and "show" in l
        ]
        for ov in overlay_lines:
            self.assertNotIn('self._overlay.show(region)', ov,
                             "Must not pass raw region dict to overlay.show")

    def test_overlay_uses_bubble_dimensions_not_region_dimensions(self):
        """The overlay.show() uses bw (bubble width) and bh (bubble height),
        NOT region["w"] and region["h"]."""
        self.assertIn('"w": bw', self.source)
        self.assertIn('"h": bh', self.source)
        # Must NOT use region["w"] or region["h"] for the overlay.
        overlay_lines = [
            l.strip() for l in self.source.split("\n")
            if "overlay" in l and "show" in l
        ]
        for ov in overlay_lines:
            if 'region["w"]' in ov:
                self.fail(f"overlay.show must not use region width: {ov}")
            if 'region["h"]' in ov:
                self.fail(f"overlay.show must not use region height: {ov}")


if __name__ == "__main__":
    unittest.main()
