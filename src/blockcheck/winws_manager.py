import subprocess
import threading
from typing import List, Optional


class WinWSManager:
    """Manages winws.exe process lifecycle with minimal latency."""
    
    _SUCCESS_MARKER = b"windivert initialized. capture is started."
    
    def __init__(self, winws_path: str, bin_dir: str):
        self.winws_path: str = winws_path
        self.bin_dir: str = bin_dir
        self.process: Optional[subprocess.Popen] = None
        
        self._ready_event = threading.Event()
        self._crashed = False
        self._stderr_lines: List[str] = []
        self._threads: List[threading.Thread] = []

    def _monitor_stdout(self, stream):
        """Scans stdout for success marker, exits immediately on detection."""
        try:
            for line in iter(stream.readline, b''):
                if self._SUCCESS_MARKER in line:
                    self._ready_event.set()
                    return
        except (IOError, ValueError):
            pass

    def _monitor_stderr(self, stream):
        """Collects stderr output; first non-empty line signals crash."""
        try:
            for line in iter(stream.readline, b''):
                try:
                    decoded = line.decode('utf-8', errors='replace')
                    self._stderr_lines.append(decoded)
                    if not self._crashed and decoded.strip():
                        self._crashed = True
                        self._ready_event.set()
                except Exception:
                    pass
        except (IOError, ValueError):
            pass

    def start(self, params: List[str], timeout: float = 5.0) -> bool:
        """
        Starts winws.exe and waits for ready signal.
        
        Returns:
            True if process started successfully, False otherwise.
        """
        if self.process:
            self.stop()

        self._ready_event.clear()
        self._crashed = False
        self._stderr_lines.clear()
        self._threads.clear()

        cmd = [self.winws_path] + params
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.bin_dir
            )
        except (OSError, FileNotFoundError) as e:
            self._stderr_lines.append(f"Failed to start: {e}\n")
            self.process = None
            return False
        
        # Start monitoring threads
        stdout_thread = threading.Thread(
            target=self._monitor_stdout,
            args=(self.process.stdout,),
            daemon=False
        )
        stderr_thread = threading.Thread(
            target=self._monitor_stderr,
            args=(self.process.stderr,),
            daemon=False
        )
        
        stdout_thread.start()
        stderr_thread.start()
        self._threads = [stdout_thread, stderr_thread]

        # Wait for ready or crash signal
        ready = self._ready_event.wait(timeout=timeout)
        
        if ready and not self._crashed:
            return True
        
        self.stop()
        return False

    def stop(self):
        """Terminates the process and joins monitoring threads."""
        if not self.process:
            self._join_threads(timeout=0.3)
            return
        
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        except (ProcessLookupError, OSError):
            pass
        finally:
            self.process = None
        
        self._join_threads(timeout=0.5)

    def _join_threads(self, timeout: float):
        """Waits for monitoring threads to finish."""
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=timeout)
        self._threads.clear()

    def get_stderr(self) -> str:
        """Returns captured stderr output as a single string."""
        return ''.join(self._stderr_lines)
