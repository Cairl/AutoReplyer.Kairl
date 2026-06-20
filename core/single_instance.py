"""
Single-instance enforcement: a new launch terminates the previous instance.

Uses a PID file in APPDATA. On startup we read the old PID, terminate that
process if alive, then write our own PID. The PID file is cleaned up on exit.
"""
import os
import time
import atexit
import subprocess

_PID_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "WeAutoReplyer",
    "instance.pid",
)

# CREATE_NO_WINDOW — prevents console pop-up from tasklist/taskkill.
_NO_WINDOW = 0x08000000


def _is_alive(pid: int) -> bool:
    """Return True if a process with `pid` is currently running (Windows)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def _terminate(pid: int):
    """Force-terminate the process with `pid` (Windows)."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, creationflags=_NO_WINDOW,
        )
    except Exception:
        pass


def claim_single_instance():
    """Terminate any running instance, then register this process as the one.

    Must be called once at startup, before any other initialization. Cleans up
    the PID file on process exit via atexit.
    """
    # Try to read and terminate the previous instance.
    try:
        with open(_PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        old_pid = None

    if old_pid and old_pid != os.getpid() and _is_alive(old_pid):
        _terminate(old_pid)
        # Wait for the old instance to fully exit so resources (overlay
        # windows, clipboard) are released before we proceed.
        for _ in range(50):  # up to 5 seconds
            if not _is_alive(old_pid):
                break
            time.sleep(0.1)

    # Write our PID.
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    atexit.register(_release)


def _release():
    """Remove the PID file on exit, but only if it still belongs to us."""
    try:
        with open(_PID_FILE, "r") as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            os.remove(_PID_FILE)
    except (FileNotFoundError, ValueError, OSError):
        pass
