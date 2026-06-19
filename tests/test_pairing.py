"""Verify received->reply pairing in _analyze_region against interleaved-message
scenarios. Uses synthetic screenshots so no live WeChat window is needed.

Run: python tests/test_pairing.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image

from core.monitor import Monitor


def make_monitor():
    """Build a Monitor without running its __init__ (which needs a Config + OCR)."""
    m = Monitor.__new__(Monitor)
    return m


def make_image(width, height, bg=(255, 255, 255)):
    arr = np.full((height, width, 3), bg, dtype=np.uint8)
    return arr


def draw_recv(arr, x, y, w, h, color=(47, 47, 48)):
    """Draw a received bubble (solid fill) on the array."""
    arr[y:y + h, x:x + w] = color


def draw_reply(arr, x, y, w, h, color=(157, 242, 159)):
    """Draw a green self-reply bubble on the array."""
    arr[y:y + h, x:x + w] = color


def analyze(arr):
    img = Image.fromarray(arr, "RGB")
    m = make_monitor()
    return m._analyze_region(img)


def bbox(arr, label, x, y, w, h, color):
    if label == "recv":
        draw_recv(arr, x, y, w, h, color)
    else:
        draw_reply(arr, x, y, w, h, color)


def scenario_interleaved():
    """User's reported case:
       A(@all) -> R1(2) -> B(test) -> C(test) -> R2(2)
       Lowest received = C; C pairs with R2 -> already_replied=True.
       Even if only A survives color match, A pairs with R1 -> still replied.
    """
    arr = make_image(400, 260)
    draw_recv(arr, 20, 20, 120, 30)    # A @all
    draw_reply(arr, 240, 60, 100, 25)  # R1 (A's reply)
    draw_recv(arr, 20, 100, 90, 28)    # B test
    draw_recv(arr, 20, 140, 90, 28)    # C test (lowest recv)
    draw_reply(arr, 240, 180, 100, 25) # R2 (C's reply)
    view = analyze(arr)
    assert view["bubble"] is not None, "bubble should be found"
    assert view["already_replied"] is True, "lowest recv C must pair with R2"
    assert view["reply_box"] is not None, "reply_box must be set when paired"
    print("[OK] interleaved: lowest recv C paired with R2, already_replied=True")


def scenario_only_atall_with_reply():
    """A(@all) is the lowest received, R1 sits below it -> already replied."""
    arr = make_image(400, 120)
    draw_recv(arr, 20, 20, 120, 30)    # A @all (lowest)
    draw_reply(arr, 240, 60, 100, 25)  # R1 (A's reply)
    view = analyze(arr)
    assert view["already_replied"] is True, "A must pair with R1"
    print("[OK] single @all + reply: paired, already_replied=True")


def scenario_atall_no_reply():
    """A(@all) lowest, no green reply -> NOT replied -> should trigger."""
    arr = make_image(400, 80)
    draw_recv(arr, 20, 20, 120, 30)    # A @all (lowest, no reply)
    view = analyze(arr)
    assert view["already_replied"] is False, "no reply -> not replied"
    assert view["reply_box"] is None
    print("[OK] @all no reply: already_replied=False (would trigger)")


def scenario_reply_above_next_recv_not_stolen():
    """A(@all) -> R1 -> B(test).
       R1 must pair with A (above B), NOT with B. B (lowest) has no reply."""
    arr = make_image(400, 160)
    draw_recv(arr, 20, 20, 120, 30)    # A @all
    draw_reply(arr, 240, 55, 100, 25)  # R1
    draw_recv(arr, 20, 95, 90, 28)     # B test (lowest)
    view = analyze(arr)
    # Lowest = B; R1 was claimed by A, so B is NOT replied.
    assert view["already_replied"] is False, "B must not steal A's reply R1"
    print("[OK] reply stays with upper recv; lower recv correctly unpaired")


def scenario_no_reply_for_lowest_but_reply_for_upper():
    """A(@all) -> R1 -> B(@all, lowest, no reply).
       A pairs R1; B has no reply -> B would trigger (correct: B is unanswered)."""
    arr = make_image(400, 160)
    draw_recv(arr, 20, 20, 120, 30)    # A @all
    draw_reply(arr, 240, 55, 100, 25)  # R1 (A's reply)
    draw_recv(arr, 20, 95, 120, 30)    # B @all (lowest, no reply)
    view = analyze(arr)
    assert view["already_replied"] is False, "B has no reply -> trigger"
    print("[OK] upper answered, lower unanswered: lower triggers correctly")


def scenario_day_mode():
    """Same as interleaved but with day-mode received color #EEEEF0."""
    arr = make_image(400, 260)
    draw_recv(arr, 20, 20, 120, 30, color=(238, 238, 240))   # A day-mode
    draw_reply(arr, 240, 60, 100, 25, color=(157, 242, 159))
    draw_recv(arr, 20, 100, 90, 28, color=(238, 238, 240))   # B
    draw_recv(arr, 20, 140, 90, 28, color=(238, 238, 240))   # C lowest
    draw_reply(arr, 240, 180, 100, 25, color=(157, 242, 159))
    view = analyze(arr)
    assert view["already_replied"] is True, "day-mode: C must pair with R2"
    print("[OK] day-mode colors: pairing works")


def scenario_dark_reply_color():
    """Use dark-mode green #35D28D for replies."""
    arr = make_image(400, 120)
    draw_recv(arr, 20, 20, 120, 30)
    draw_reply(arr, 240, 60, 100, 25, color=(53, 210, 141))  # dark green
    view = analyze(arr)
    assert view["already_replied"] is True, "dark-green reply must be detected"
    print("[OK] dark-mode green reply detected")


if __name__ == "__main__":
    scenario_interleaved()
    scenario_only_atall_with_reply()
    scenario_atall_no_reply()
    scenario_reply_above_next_recv_not_stolen()
    scenario_no_reply_for_lowest_but_reply_for_upper()
    scenario_day_mode()
    scenario_dark_reply_color()
    print("\nAll pairing tests passed.")
