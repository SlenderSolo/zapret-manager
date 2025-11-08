import asyncio
import subprocess
from typing import List, Optional, Literal

class _AsyncWinWSManager:
    """Internal async implementation of WinWS manager."""
    
    def __init__(self, winws_path: str, bin_dir: str):
        self.winws_path: str = winws_path
        self.bin_dir: str = bin_dir
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stderr_lines: List[str] = []
        
        self._outcome: Optional[Literal['ready', 'crashed']] = None
        self._outcome_event = asyncio.Event()
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

    async def _read_stream(
        self, 
        stream: asyncio.StreamReader, 
        outcome_on_output: Literal['ready', 'crashed'], 
        success_keyword: Optional[str] = None
    ):
        """Asynchronously reads a stream and sets the outcome."""
        try:
            while True:
                line_bytes = await stream.readline()
                if not line_bytes:
                    break
                    
                line = line_bytes.decode('utf-8', errors='replace')
                
                if outcome_on_output == 'crashed':
                    self.stderr_lines.append(line)

                should_trigger = (
                    (success_keyword and success_keyword in line) or 
                    (not success_keyword and line.strip())
                )
                
                if should_trigger and self._outcome is None:
                    self._outcome = outcome_on_output
                    self._outcome_event.set()
                    if outcome_on_output == 'ready':
                        return
                        
        except (asyncio.CancelledError, Exception):
            pass

    async def start(self, params: List[str], timeout: float = 5.0) -> bool:
        """Starts winws.exe and waits for ready signal."""
        if self.process:
            await self.stop()

        cmd = [self.winws_path] + params
        self.stderr_lines.clear()
        self._outcome = None
        self._outcome_event.clear()

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.bin_dir
            )
        except (OSError, FileNotFoundError) as e:
            self.stderr_lines.append(f"Failed to start process: {e}")
            self.process = None
            return False
        
        self._stdout_task = asyncio.create_task(
            self._read_stream(
                self.process.stdout, 
                'ready', 
                "windivert initialized. capture is started."
            )
        )
        
        self._stderr_task = asyncio.create_task(
            self._read_stream(self.process.stderr, 'crashed')
        )

        try:
            await asyncio.wait_for(self._outcome_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        if self._outcome == 'ready':
            return True
        else:
            await self.stop()
            return False

    async def stop(self):
        """Stops the winws.exe process and cleans up resources."""
        if self._stdout_task and not self._stdout_task.done():
            self._stdout_task.cancel()
            try:
                await asyncio.wait_for(self._stdout_task, timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
                
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await asyncio.wait_for(self._stderr_task, timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        
        self._stdout_task = None
        self._stderr_task = None
        
        if not self.process:
            return
            
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
        except (ProcessLookupError, Exception) as e:
            if not isinstance(e, ProcessLookupError):
                self.stderr_lines.append(f"Error during process stop: {e}")
        finally:
            self.process = None

    def get_stderr(self) -> str:
        """Returns all captured stderr output."""
        return "".join(self.stderr_lines)


class WinWSManager:
    """Synchronous wrapper around async WinWS manager."""

    def __init__(self, winws_path: str, bin_dir: str):
        self._async_manager = _AsyncWinWSManager(winws_path, bin_dir)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Ensures we have a running event loop."""
        try:
            asyncio.get_running_loop()
            # We're inside an async context, can't use run_until_complete
            raise RuntimeError("WinWSManager sync methods cannot be called from async context")
        except RuntimeError:
            # No running loop - create one for sync operations
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop
    
    def start(self, params: List[str], timeout: float = 5.0) -> bool:
        """
        Starts the winws.exe process (synchronous interface).
        
        Returns:
            True if process started successfully, False otherwise.
        """
        loop = self._ensure_loop()
        return loop.run_until_complete(self._async_manager.start(params, timeout))
    
    def stop(self):
        """Stops the winws.exe process (synchronous interface)."""
        loop = self._ensure_loop()
        loop.run_until_complete(self._async_manager.stop())
    
    def get_stderr(self) -> str:
        """Returns all captured stderr output."""
        return self._async_manager.get_stderr()
