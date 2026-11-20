import ctypes
import os
import subprocess
import sys
import threading
import time


def enable_ansi_support():
    """Enables ANSI escape code support in the Windows console."""
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                mode.value |= 0x0004
                kernel32.SetConsoleMode(handle, mode)
        except (AttributeError, OSError):
            pass


def is_admin() -> bool:
    """Checks for administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except AttributeError:
        return False


def run_as_admin():
    """Restarts the script with administrator privileges if required."""
    if not is_admin():
        print("Administrator privileges are required.")
        print("Attempting to restart with administrator privileges...")
        try:
            script = os.path.abspath(sys.argv[0])
            params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}" {params}', None, 1)
            sys.exit(0)
        except Exception as e:
            print(f"Failed to automatically elevate privileges: {e}")
            print("Please, re-run this script as an administrator.")
            input("Press Enter to exit.")
            sys.exit(1)


def is_process_running(process_name: str) -> bool:
    """Checks if a process is running by its name."""
    try:
        result = subprocess.run(
            ["tasklist.exe", "/NH", "/FI", f"IMAGENAME eq {process_name}.exe"],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=3,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return f"{process_name}.exe".lower() in result.stdout.lower()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def kill_process(process_name: str) -> bool:
    """Terminates all processes matching process_name via taskkill."""
    try:
        result = subprocess.run(
            ["taskkill.exe", "/F", "/IM", f"{process_name}.exe"],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=3,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"Error killing process {process_name}: {e}")
        return False

    # 0 = terminated, 128 = process not found
    if result.returncode in (0, 128):
        return True

    err = (result.stderr or result.stdout).strip()
    if err:
        print(f"Error killing process {process_name}: {err}")
    return False


class TokenBucket:
    """A simple token bucket implementation for rate limiting."""

    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self.refill_rate = float(refill_rate)
        self.last_update = time.monotonic()
        self._lock = threading.Lock()

    def wait_for_token(self, tokens: int = 1):
        """Blocks the calling thread until enough tokens are available."""
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self.last_update) * self.refill_rate) - tokens
            self.last_update = now

            if self._tokens >= 0:
                return
            wait_time = -self._tokens / self.refill_rate

        time.sleep(wait_time)
