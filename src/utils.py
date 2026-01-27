import ctypes
import os
import subprocess
import sys
import time
import threading

def enable_ansi_support():
    """Enables ANSI escape code support in the Windows console."""
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                mode.value |= 0x0004
                kernel32.SetConsoleMode(handle, mode)
        except (AttributeError, ctypes.WinError):
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
            ['tasklist', '/NH', '/FI', f'IMAGENAME eq {process_name}.exe'],
            capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return f"{process_name}.exe" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def kill_process(process_name: str) -> bool:
    """Terminates a process by name using taskkill."""
    try:
        result = subprocess.run(
            ['taskkill', '/F', '/IM', f'{process_name}.exe', '/T'],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.returncode == 0 or "not found" in result.stderr.lower()
    except Exception as e:
        print(f"Error killing process {process_name}: {e}")
        return False

class TokenBucket:
    """A simple token bucket implementation for rate limiting."""
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self.refill_rate = float(refill_rate)
        self.last_update = time.monotonic()
        self._condition = threading.Condition()

    def wait_for_token(self, tokens: int = 1):
        """Blocks the calling thread until enough tokens are available."""
        with self._condition:
            while True:
                now = time.monotonic()
                time_passed = now - self.last_update
                self.last_update = now
                
                # Refill tokens
                self._tokens = min(self.capacity, self._tokens + (time_passed * self.refill_rate))
                
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                
                # Calculate exact sleep time needed
                deficit = tokens - self._tokens
                wait_time = deficit / self.refill_rate
                
                # Wait releases the lock and blocks this thread
                self._condition.wait(timeout=wait_time)
                