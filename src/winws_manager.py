import subprocess
import threading
import time

class WinWSManager:
    """Manages the lifecycle of a single winws.exe process."""
    def __init__(self, winws_path, bin_dir):
        self.winws_path = winws_path
        self.bin_dir = bin_dir
        self.process = None
        self.stderr_lines = []
        self._stderr_thread = None

    def _read_stderr(self):
        """Continuously reads stderr from the process and stores it."""
        try:
            for line in iter(self.process.stderr.readline, ''):
                self.stderr_lines.append(line)
        except (IOError, ValueError):
            pass # Process stream was closed

    def start(self, params: list[str]) -> bool:
        """
        Starts the winws.exe process with the given parameters and waits for it to be ready.
        Returns True if successful, False otherwise.
        """
        self.stop() # Ensure no previous process is running

        cmd = [self.winws_path] + params
        self.stderr_lines.clear()

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.bin_dir
            )
        except OSError as e:
            self.stderr_lines.append(f"Failed to start process: {e}")
            self.process = None
            return False

        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        ready_event = threading.Event()

        def _wait_for_ready(proc, event):
            try:
                for line in iter(proc.stdout.readline, ''):
                    if "windivert initialized. capture is started." in line:
                        event.set()
                        return
            except (IOError, ValueError):
                pass # Process stream was closed

        ready_thread = threading.Thread(target=_wait_for_ready, args=(self.process, ready_event), daemon=True)
        ready_thread.start()

        timeout_seconds = 3
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout_seconds:
            if ready_event.is_set():
                return True
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        self.stop()
        return False

    def stop(self):
        """Stops the winws.exe process if it's running."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception:
                pass # Ignore other errors during shutdown
            self.process = None

    def is_crashed(self) -> bool:
        """Checks if the process has terminated unexpectedly."""
        if not self.process:
            return False
        return self.process.poll() is not None

    def get_stderr(self) -> str:
        """Returns the captured stderr output as a single string."""
        return "".join(self.stderr_lines)