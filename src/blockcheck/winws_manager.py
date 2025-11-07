import subprocess
import threading
from typing import List, Optional, Literal

class WinWSManager:
    """
    Manages the lifecycle of a single winws.exe process.

    This class provides a robust way to start, stop, and monitor the winws.exe
    process. It uses a thread-based, event-driven approach to determine the
    process state (ready or crashed) by parsing stdout and stderr in real-time.
    This avoids simple time-based waits and provides a more definitive status.
    """
    def __init__(self, winws_path: str, bin_dir: str):
        self.winws_path: str = winws_path
        self.bin_dir: str = bin_dir
        self.process: Optional[subprocess.Popen] = None
        self.stderr_lines: List[str] = []
        
        self._lock = threading.Condition()
        self._outcome: Optional[Literal['ready', 'crashed']] = None
        
        # Track threads to ensure proper cleanup
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    def _read_stream(self, stream, outcome_on_output: Literal['ready', 'crashed'], success_keyword: Optional[str] = None):
        """
        Generic function to read a stream (stdout/stderr) and set the outcome.
        """
        try:
            for line_bytes in iter(stream.readline, b''):
                if self._stop_flag.is_set():
                    break
                    
                line = line_bytes.decode('utf-8', errors='replace')
                if stream == self.process.stderr:
                    self.stderr_lines.append(line)

                # If a keyword is provided, we need it to declare success.
                # If no keyword, any output triggers the outcome.
                if (success_keyword and success_keyword in line) or \
                   (not success_keyword and line.strip()):
                    with self._lock:
                        if self._outcome is None:
                            self._outcome = outcome_on_output
                            self._lock.notify()
                    if outcome_on_output == 'ready':
                        return
        except (IOError, ValueError):
            pass

    def _cleanup_threads(self, timeout: float = 2.0):
        """
        Properly join all monitoring threads.
        This prevents thread leaks.
        """
        self._stop_flag.set()
        
        threads_to_join = []
        if self._stdout_thread and self._stdout_thread.is_alive():
            threads_to_join.append(('stdout', self._stdout_thread))
        if self._stderr_thread and self._stderr_thread.is_alive():
            threads_to_join.append(('stderr', self._stderr_thread))
        
        for name, thread in threads_to_join:
            thread.join(timeout=timeout)
            if thread.is_alive():
                pass
        
        self._stdout_thread = None
        self._stderr_thread = None
        self._stop_flag.clear()

    def start(self, params: List[str], timeout: float = 5.0) -> bool:
        """
        Starts the winws.exe process and waits for a definitive signal.

        Args:
            params: A list of command-line arguments for winws.exe.
            timeout: The maximum time in seconds to wait for a ready/crash signal.

        Returns:
            True if the process starts successfully (i.e., "ready" signal received).
            False if the process crashes, fails to start, or times out.
        """
        if self.process:
            self.stop()

        cmd = [self.winws_path] + params
        self.stderr_lines.clear()
        self._outcome = None
        self._stop_flag.clear()

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.bin_dir
            )
        except (OSError, FileNotFoundError) as e:
            self.stderr_lines.append(f"Failed to start process: {e}")
            self.process = None
            return False
        
        # Monitor stdout for the "ready" signal
        self._stdout_thread = threading.Thread(
            target=self._read_stream,
            args=(self.process.stdout, 'ready', "windivert initialized. capture is started."),
            daemon=False,
            name="WinWS-stdout-monitor"
        )
        
        # Monitor stderr for any output, which indicates a crash
        self._stderr_thread = threading.Thread(
            target=self._read_stream,
            args=(self.process.stderr, 'crashed'),
            daemon=False,
            name="WinWS-stderr-monitor"
        )
        
        self._stdout_thread.start()
        self._stderr_thread.start()

        with self._lock:
            if self._outcome is None:
                self._lock.wait(timeout=timeout)

        if self._outcome == 'ready':
            return True
        else:
            self.stop()
            return False

    def stop(self):
        """
        Stops the winws.exe process forcefully if it is running.
        """
        if not self.process:
            self._cleanup_threads(timeout=0.5)
            return
            
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        except (ProcessLookupError, OSError):
            pass
        except Exception as e:
            self.stderr_lines.append(f"Error during process stop: {e}")
        finally:
            self.process = None
            self._cleanup_threads(timeout=1.0)

    def get_stderr(self) -> str:
        """Returns all captured stderr output as a single string."""
        return "".join(self.stderr_lines)
