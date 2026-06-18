# AutoReplyer.Kairl

WeChat Group @all Auto-Reply Tool — Python TUI with OCR-based screen monitoring.

## Architecture

```
AutoReplyer.Kairl/
  main.py          # Complete application (TUI + Monitor + Region Selector)
  config.json      # Auto-generated runtime configuration (hot-reloadable)
  start.bat        # Launch script
  AGENTS.md        # This file
```

## Tech Stack

- Python 3.10+ (Windows)
- msvcrt: non-blocking keyboard input
- PyAutoGUI: screen capture, mouse/keyboard automation
- Pillow: image processing
- winocr: Windows built-in OCR engine (no torch dependency)
- pywin32 (win32clipboard): Chinese clipboard support
- tkinter: region selection overlay (stdlib)

## Dependencies

```
pip install pyautogui pillow pywin32 winocr
```

## How It Works

1. **TUI Configuration** — Main menu with Reply Settings and Group Management
2. **Region Selection** — Full-screen screenshot overlay, drag to select message/reply areas
3. **Monitoring** — Background thread captures message regions via screenshot, runs PaddleOCR to detect @all, auto-replies via clipboard paste

## Key Design Decisions

- Single-file architecture (like SeeingSort): all logic in main.py
- Incremental rendering: only redraw changed lines, zero flicker
- Config hot-reload: monitor reads config.json changes without restart
- Per-group regions: each group has independent message_area and reply_area coordinates
- Debounce: 30-second cooldown per group prevents duplicate replies
- Atomic config writes: temp file + os.replace prevents corruption

## TUI Navigation

- Pure cursor navigation: Up/Down/Enter/Esc only
- All operations via cursor navigation + Enter confirmation
- Reply settings: Enter to edit inline, Enter to confirm, Esc to cancel
- Groups: Enter to open action sub-menu (toggle, set regions, delete)
- Sub-menus for group actions: cursor navigation within overlay
- Bottom hint bar: `[Up/Down] 导航    [Enter] 确认    [Esc] 返回`
- Inline text editing for reply settings (supports Chinese via getwch)

## User Preferences

- No emoji in UI
- Dark theme, background not below #18181b
- Labels must be complete, never abbreviated
- Settings in dedicated panels, not crammed into toolbar
- Single-page compact layout, no pre-built pagination/sidebar
- Character-level precision in TUI alignment
- Highlight preserves original font color, only adds background
