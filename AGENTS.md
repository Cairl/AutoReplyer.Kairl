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
    overlay.py         # Red rectangle overlay: click-through tkinter window showing captured region
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
3. **Monitoring** — Background thread captures the message region, locates the bottom-left chat bubble by color (#2F2F30 night / #EEEEF0 day), OCR-scans only that latest received bubble for trigger keywords, and auto-replies via clipboard paste ONLY when no self-sent reply bubble (green #9DF29F light / #35D28D dark) sits at or below it. A short-lived in-flight guard covers the reply-delay window. Each capture flashes a red rectangle (click-through tkinter overlay) around the scanned region. Monitoring per group is toggled on/off from the group's action menu and persisted in config.json (the `enabled` field).

## Key Design Decisions

- Two-folder structure: `core/` for data/logic, `ui/` for terminal rendering
- Incremental rendering: only redraw changed lines, zero flicker
- Config hot-reload: monitor reads config.json changes without restart
- Per-group regions: each group has independent message_area and reply_area coordinates
- Bubble-color detection: matches #2F2F30 (night) or #EEEEF0 (day) in the left half of the region to isolate the latest received message; self-sent bubbles are detected separately by their green color (#9DF29F light / #35D28D dark) across the whole region
- Screen-based "already replied" check: a trigger fires only when the keyword bubble has no green reply bubble at or below it — so stale on-screen messages aren't re-replied and the same keyword sent again after an answer is correctly re-replied. A per-group in-flight guard (keyed by the trigger bubble's position+text signature) prevents double-firing during the reply delay, before the green bubble renders
- Atomic config writes: temp file + os.replace prevents corruption
- Auto-named groups: "群 1", "群 2", etc. — no manual naming needed

## TUI Navigation

- Pure cursor navigation: Up/Down/Enter/Esc only
- All operations via cursor navigation + Enter confirmation
- Reply settings: Enter to edit inline, Enter to confirm, Esc to cancel
- Groups: Enter to open action sub-menu (toggle, set regions, delete)
- Group actions: [选择消息位置] [选择输入位置] [删除] [监测: 开/关] — Left/Right to switch, Enter to execute. The 监测 action toggles per-group monitoring (green ● = on, red ○ = off), persisted to config.json
