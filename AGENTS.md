# WeAutoReplyer

WeChat Group @all Auto-Reply Tool — Python TUI with OCR-based screen monitoring.

## Architecture

```
WeAutoReplyer/
  main.py              # Entry point
  core/
    __init__.py
    config.py          # Config manager: auto-generation, field repair, hot-reload
    monitor.py         # Background OCR monitor: pair-detection, auto-reply, notification
    overlay.py         # Dual-rectangle overlay: red (received) + green (reply), click-through tkinter
    region.py          # Screen region selector: PySide6 QDialog overlay
    gpu.py             # GPU acceleration: hard-requires NVIDIA driver + CuPy + CUDA Toolkit
    single_instance.py # Single-instance enforcement: new launch terminates the previous one
  ui/
    __init__.py
    ui.py              # Colors, box-drawing, display helpers, keyboard input
    tui.py             # TUI application: rendering, navigation, all screens
  config.json          # Auto-generated runtime configuration
  CHANGELOG.md
  AGENTS.md
```

## Tech Stack

- Python 3.10+ (Windows)
- msvcrt: non-blocking keyboard input
- PyAutoGUI: screen capture, mouse/keyboard automation
- Pillow: image processing
- numpy: bubble color-matching (#2F2F30 / #EEEEF0) + pair detection (std)
- CuPy: GPU-accelerated array ops (hard requirement, no CPU fallback)
- winocr: Windows built-in OCR engine (no torch dependency)
- pywin32 (win32clipboard): Chinese clipboard support + shortcut creation
- PySide6 (QDialog): region selector overlay (from MusicClassifier)
- winotify: Windows toast notification after reply

## Dependencies

```
pip install pyautogui pillow numpy cupy-cuda12x pywin32 winocr PySide6 winotify
```

## How It Works

1. **Single Instance** — On startup, terminates any running instance via PID file (`%APPDATA%/WeAutoReplyer/instance.pid`) + `taskkill /F`, then registers its own PID. PID file cleaned up on exit via `atexit`.
2. **TUI Configuration** — Main menu with Reply Settings and Group Settings
3. **Region Selection** — PySide6 QDialog full-screen overlay with dark mask + white border + crosshair + confirm/cancel toolbar, DPI-aware via `devicePixelRatio()`
4. **Monitoring** — Background thread with 3-second startup delay. Captures the message region, locates the bottom-left chat bubble by color (#2F2F30 night / #EEEEF0 day), clicks the bubble to expose hidden content, OCR-scans the latest received bubble for trigger keywords.
5. **Trigger Matching** — Detection content is treated as a regex pattern. OCR text is normalized (all whitespace removed, `一`→`-` for misrecognized hyphens) before `re.search()`. Invalid regex silently skips.
6. **Pair Detection** — Screenshots the right-half area below the received bubble, computes `np.std()`: high variance = reply content exists below = already replied → skip; low variance = no reply → trigger.
7. **Auto-Reply** — Clipboard paste via `win32clipboard`, Ctrl+V + Enter. Random delay (delay_min ~ delay_max). In-flight guard (bubble signature) prevents duplicate firing during reply render window.
8. **Visual Overlay** — Red rectangle around received bubble, green rectangle around reply content (if detected). Click-through tkinter window, 0.5s hold.
9. **Notification** — Windows toast via `winotify` with AUMID shortcut auto-registration on first use.

## Key Design Decisions

- Two-folder structure: `core/` for data/logic, `ui/` for terminal rendering
- Single-instance via PID file: new launch kills old, no mutex/lock complexity
- GPU environment is a hard requirement: missing NVIDIA driver / CuPy / CUDA Toolkit raises immediately, no silent CPU fallback
- Incremental rendering: only redraw changed lines, zero flicker
- Config hot-reload: monitor reads config.json changes without restart
- Per-group regions: each group has independent message_area and reply_area coordinates
- Bubble-color detection: matches #2F2F30 (night) or #EEEEF0 (day) in the left half
- Pair-detection "already replied" check: `np.std()` on receiver bubble's below-right area → no green-color dependency, survives restarts, handles obscured content
- In-flight guard: `MD5(row|w|h|text)` signature with `delay_max + 5s` expiry per group
- Click-to-expose: keyword match clicks the bubble, re-screenshots, re-analyzes pair before replying
- No persistent history: pure screen-based detection, zero disk state beyond config.json
- Atomic config writes: temp file + os.replace prevents corruption
- Auto-named groups: "群 1", "群 2", etc. — no manual naming needed
- Startup delay: 3-second no-scan window on launch, TUI shows yellow "启动中"
- OCR line extraction: reads `result["lines"]` (per-line `text`) and joins with `\n` to preserve line breaks for display; top-level `result["text"]` joins all lines with spaces and loses breaks
- OCR normalization: Windows OCR inserts spaces between CJK chars and misrecognizes `-` as `一`; matching text strips all whitespace and maps `一`→`-`
- Edit-mode input uses `msvcrt.getch()` (raw bytes), not `getwch()` — `getwch` merges `\xe0` + scan_code into a single character under CP936/GBK, making extended keys undetectable. GBK double-byte lead bytes (0x81-0xFE) in the printable path are decoded by reading a second `getch()` and combining.

## TUI Navigation

- Cursor navigation: Up/Down/Enter/Esc
- Reply settings: Enter to edit inline, Enter to confirm, Esc to cancel
- Groups: Enter to open action sub-menu (select regions, delete)
- Group actions: [选择消息位置] [选择输入位置] [删除] — Left/Right to switch, Enter to execute
- 运行状态行: 位于硬件加速下方 — Enter 切换 已启动/未启动，每次启动默认未启动
- 识别内容区: 位于回复设置下方 — 显示 OCR 原始文本（保留换行），不可选
