import subprocess
import threading

class WinWSManager:
    """
    Manages the lifecycle of a single winws.exe process.
    This version uses a definitive, event-driven approach based on parsing
    stdout and stderr to determine the process state (ready or crashed).
    """
    def __init__(self, winws_path, bin_dir):
        self.winws_path = winws_path
        self.bin_dir = bin_dir
        self.process = None
        self.stderr_lines = []
        self._lock = threading.Condition()
        self._outcome = None

    def _read_stderr_and_store(self):
        """
        Dedicated function to read stderr and append lines for later retrieval.
        This runs in a separate thread.
        """
        try:
            for line_bytes in iter(self.process.stderr.readline, b''):
                line = line_bytes.decode('utf-8', errors='replace')
                self.stderr_lines.append(line)
                
                if line.strip():
                    with self._lock:
                        if self._outcome is None:
                            self._outcome = 'crashed'
                            self._lock.notify()
        except (IOError, ValueError):
            pass

    def start(self, params: list[str]) -> bool:
        """
        Starts the winws.exe process and waits for a definitive signal:
        - The "ready" message on STDOUT (Success).
        - Any message on STDERR (Failure).
        - A timeout if neither signal is received (Failure).
        Returns True on success, False otherwise.
        """
        self.stop() 

        cmd = [str(self.winws_path)] + params
        self.stderr_lines.clear()
        self._outcome = None

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.bin_dir
            )
        except OSError as e:
            self.stderr_lines.append(f"Failed to start process: {e}")
            self.process = None
            return False
        
        stderr_monitor_thread = threading.Thread(target=self._read_stderr_and_store, daemon=True)
        stderr_monitor_thread.start()

        def _monitor_stdout():
            try:
                for line_bytes in iter(self.process.stdout.readline, b''):
                    line = line_bytes.decode('utf-8', errors='replace')
                    if "windivert initialized. capture is started." in line:
                        with self._lock:
                            if self._outcome is None:
                                self._outcome = 'ready'
                                self._lock.notify()
                        return
            except (IOError, ValueError):
                pass
        
        stdout_monitor_thread = threading.Thread(target=_monitor_stdout, daemon=True)
        stdout_monitor_thread.start()

        with self._lock:
            if self._outcome is None:
                self._lock.wait(timeout=5.0)

        if self._outcome == 'ready':
            return True
        else:
            self.stop()
            stderr_monitor_thread.join(timeout=0.5) 
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
                pass
            self.process = None

    def get_stderr(self) -> str:
        """Returns the captured stderr output as a single string."""
        return "".join(self.stderr_lines)