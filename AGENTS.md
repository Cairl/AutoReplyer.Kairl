# AutoReplyer.Kairl

WeChat Group @all Auto-Reply Tool — Python TUI with OCR-based screen monitoring.

## Architecture

```
AutoReplyer.Kairl/
  main.py              # Entry point
  core/
    __init__.py
    config.py          # Config manager: auto-generation, field repair, hot-reload
    monitor.py         # Background OCR monitor: bubble-color detection, auto-reply
    region.py          # Screen region selector: tkinter overlay
  ui/
    __init__.py
    ui.py              # Colors, box-drawing, display helpers, keyboard input
    tui.py             # TUI application: rendering, navigation, all screens
  config.json          # Auto-generated runtime configuration
  AGENTS.md
```

## Tech Stack

- Python 3.10+ (Windows)
- msvcrt: non-blocking keyboard input
- PyAutoGUI: screen capture, mouse/keyboard automation
- Pillow: image processing
- numpy: bubble color-matching (#2F2F30 / #EEEEF0)
- winocr: Windows built-in OCR engine (no torch dependency)
- pywin32 (win32clipboard): Chinese clipboard support
- tkinter: region selection overlay (stdlib)

## Dependencies

```
pip install pyautogui pillow numpy pywin32 winocr
```

## How It Works

1. **TUI Configuration** — Main menu with Reply Settings and Group Settings
2. **Region Selection** — Full-screen screenshot overlay, drag to select message/reply areas
3. **Monitoring** — Background thread captures the message region, locates the bottom-left chat bubble by color (#2F2F30 night / #EEEEF0 day), OCR-scans only that latest received bubble for trigger keywords, auto-replies via clipboard paste. Each trigger message gets exactly one reply (md5 dedup).

## Key Design Decisions

- Two-folder structure: `core/` for data/logic, `ui/` for terminal rendering
- Incremental rendering: only redraw changed lines, zero flicker
- Config hot-reload: monitor reads config.json changes without restart
- Per-group regions: each group has independent message_area and reply_area coordinates
- Bubble-color detection: matches #2F2F30 (night) or #EEEEF0 (day) in the left half of the region to isolate the latest received message; self-sent (right-side) bubbles are excluded
- Per-message dedup: md5 hash of the last replied bubble text ensures each trigger message gets exactly one reply — no stale replies, no duplicates
- Atomic config writes: temp file + os.replace prevents corruption
- Auto-named groups: "群 1", "群 2", etc. — no manual naming needed

## TUI Navigation

- Pure cursor navigation: Up/Down/Enter/Esc only
- All operations via cursor navigation + Enter confirmation
- Reply settings: Enter to edit inline, Enter to confirm, Esc to cancel
- Groups: Enter to open action sub-menu (toggle, set regions, delete)
