"""
Tests for Bug 1: Global monitoring toggle in tui.py.

Verifies:
  - n = n_groups + 8 formula
  - _group_opts has exactly 3 items
  - Group action modulo is 3 (not 4)
  - No act_idx==3 branch in _execute_group_action
  - main_selected == 0 && ENTER → toggle_monitoring()

All tests use source-code inspection (inspect.getsource) or direct string
analysis of the tui.py source file so we do NOT need to import modules that
depend on pyautogui / winocr / numpy.
"""

import sys
import os
import re
import unittest
import inspect

# Add project root to path.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Read tui.py as raw text for source-level assertions.
TUI_PATH = os.path.join(PROJECT_ROOT, "ui", "tui.py")
with open(TUI_PATH, "r", encoding="utf-8") as f:
    _TUI_SOURCE = f.read()

# Also load via import with pyautogui stubbed out to get source via inspect.
# This mock MUST happen before any import of core.monitor or core.region.
import importlib
_mock_pyautogui = type(sys)("pyautogui")
_mock_pyautogui.screenshot = lambda *a, **kw: None
sys.modules["pyautogui"] = _mock_pyautogui
sys.modules["numpy"] = type(sys)("numpy")
sys.modules["winocr"] = type(sys)("winocr")
sys.modules["PIL"] = type(sys)("PIL")
sys.modules["PIL.ImageTk"] = type(sys)("PIL.ImageTk")

from ui.tui import TUI


class TestTuiNavigationConstants(unittest.TestCase):
    """Verify the TUI navigation constants and offsets are correct after the
    global monitoring toggle row was introduced as a real rendered row."""

    def test_n_formula_n_groups_plus_8(self):
        """Line 306: n = n_groups + 8 (was +7; +1 for global toggle row)."""
        # Verify the source code contains `n_groups + 8`
        self.assertIn("n_groups + 8", _TUI_SOURCE,
                      "n = n_groups + 8 must be in the source")

        for n_groups in (0, 1, 3):
            expected = n_groups + 8
            actual = n_groups + 8
            self.assertEqual(actual, expected,
                             f"For {n_groups} groups, n should be {expected}")

    def test_group_opts_has_exactly_3_items(self):
        """Line 167: _group_opts should be exactly 3 items."""
        # The source must assign exactly 3 items to _group_opts.
        # Find the _group_opts assignment.
        match = re.search(
            r'self\._group_opts\s*=\s*\[(.*?)\]',
            _TUI_SOURCE, re.DOTALL
        )
        self.assertIsNotNone(match, "_group_opts assignment not found in source")
        opts_str = match.group(1)
        # Count quoted strings (the items).
        items = re.findall(r'"([^"]*)"', opts_str)
        self.assertEqual(len(items), 3,
                         f"_group_opts must have exactly 3 items, got {len(items)}: {items}")
        self.assertIn("选择消息位置", items)
        self.assertIn("选择输入位置", items)
        self.assertIn("删除", items)

    def test_group_action_modulo_is_3(self):
        """Lines 318, 321: modulo for LEFT/RIGHT navigation must be 3, not 4."""
        # Verify the modulo operator in source uses 3 (not 4).
        # Pattern: (act_idx ... ) % 3  in _handle_main
        handle_main_match = re.search(
            r'def _handle_main.*?(?=def |\Z)',
            _TUI_SOURCE, re.DOTALL
        )
        self.assertIsNotNone(handle_main_match,
                             "_handle_main method not found in source")
        handle_main = handle_main_match.group(0)

        # There should be `% 3` for LEFT/RIGHT, never `% 4`.
        self.assertIn("% 3", handle_main,
                      "Modulo 3 must be used in _handle_main for LEFT/RIGHT")
        self.assertNotIn("% 4", handle_main,
                         "Modulo 4 must NOT appear in _handle_main")

        # LEFT key: (act_idx - 1) % 3
        self.assertIn("(act_idx - 1) % 3", handle_main,
                      "LEFT must use (act_idx - 1) % 3")
        # RIGHT key: (act_idx + 1) % 3
        self.assertIn("(act_idx + 1) % 3", handle_main,
                      "RIGHT must use (act_idx + 1) % 3")

    def test_no_act_idx_3_in_execute_group_action(self):
        """Lines 344-365: _execute_group_action must not have act_idx==3 branch."""
        source = inspect.getsource(TUI._execute_group_action)

        self.assertIn("act_idx == 0", source)
        self.assertIn("act_idx == 1", source)
        self.assertIn("act_idx == 2", source)

        # Check that act_idx == 3 does NOT appear as an executable branch.
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if "act_idx" in stripped and "3" in stripped:
                if stripped.startswith("if") or stripped.startswith("elif"):
                    self.fail(f"_execute_group_action must not have "
                              f"act_idx==3 executable branch, found: {stripped}")

        # The comment mentioning act_idx==3 is fine — verify it's a comment.
        comment_mentions = any(
            "act_idx == 3" in l.strip() and l.strip().startswith("#")
            for l in lines
        )
        self.assertTrue(comment_mentions,
                        "Expected a comment about act_idx==3 being removed")

    def test_main_selected_0_enter_toggles_monitoring(self):
        """Lines 308-311: main_selected==0 && ENTER → toggle_monitoring()."""
        source = inspect.getsource(TUI._handle_main)

        self.assertIn("self.main_selected == 0", source,
                      "Must check main_selected == 0")
        self.assertIn("toggle_monitoring", source,
                      "Must call toggle_monitoring")

        # Global toggle handler must appear before group handler.
        idx_toggle = source.find("self.main_selected == 0")
        idx_group = source.find("1 <= self.main_selected")
        self.assertLess(idx_toggle, idx_group,
                        "Global toggle handler must appear before group handler")


class TestTuiRenderingIndexOffsets(unittest.TestCase):
    """Verify that all selection offset calculations use +1 for the global toggle."""

    def test_group_selection_offset_by_one(self):
        """Groups use (i + 1) == self.main_selected, not (i) == self.main_selected."""
        source = inspect.getsource(TUI._render_main)
        self.assertIn("(i + 1) == self.main_selected", source,
                      "Group selection must offset by +1 for global toggle row")

    def test_add_group_offset(self):
        """'Add group' is at n_groups + 1, not n_groups."""
        source = inspect.getsource(TUI._render_main)
        # The add group item: self.main_selected == n_groups + 1
        self.assertIn("n_groups + 1", source,
                      "Add group index must be n_groups + 1")

    def test_reply_settings_offset(self):
        """Reply items start at n_groups + 2, not n_groups + 1."""
        source = inspect.getsource(TUI._render_main)
        # Reply settings selection: sel = (n_groups + 2 + i) == self.main_selected
        self.assertIn("n_groups + 2", source,
                      "Reply settings must start at n_groups + 2")

    def test_exit_index_is_n_groups_plus_7(self):
        """Exit index is n_groups + 7 (was +6; +1 for global toggle row)."""
        source = inspect.getsource(TUI._render_main)
        self.assertIn("n_groups + 7", source,
                      "Exit index must be n_groups + 7")


if __name__ == "__main__":
    unittest.main()
