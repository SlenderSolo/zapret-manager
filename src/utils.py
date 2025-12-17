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
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_rate = float(refill_rate)
        self.last_refill = time.monotonic()
        self.condition = threading.Condition()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            tokens_to_add = elapsed * self.refill_rate
            if tokens_to_add > 0:
                self.tokens = min(self.capacity, self.tokens + tokens_to_add)
                self.last_refill = now

    def wait_for_token(self, tokens: int = 1):
        """
        Wait until enough tokens are available and then consume them.
        """
        with self.condition:
            while True:
                self._refill()
                
                # Check if we have enough tokens
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                
                # Calculate wait time for required tokens
                required = tokens - self.tokens
                wait_time = max(0, required / self.refill_rate)
                
                # Wait for either timeout or notification from another thread
                self.condition.wait(timeout=wait_time)
                