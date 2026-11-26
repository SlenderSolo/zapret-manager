from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Iterable, List, Optional

from config import CURL_MAX_WORKERS, WINWS_PATH
from src import service_manager, ui
from src.utils import is_process_running

from .network_utils import CurlRunner
from .strategy import Strategy
from .winws_manager import WinwsManager, running_winws

CHECKS_CONFIG = {
    "http": {"title": "HTTP", "test_params": {"port": 80}},
    "https_tls12": {"title": "HTTPS (TLS 1.2)", "test_params": {"port": 443, "tls_version": "1.2"}},
    "https_tls13": {"title": "HTTPS (TLS 1.3)", "test_params": {"port": 443, "tls_version": "1.3"}},
    "http3": {"title": "HTTP/3 (QUIC)", "test_params": {"port": 443, "http3_only": True}},
}


class PrerequisiteError(Exception):
    """Raised when required files are missing or an active Zapret process cannot be terminated."""

    pass


def check_prerequisites(required_files: Iterable[Path]) -> None:
    """Validates required files and ensures no conflicting Zapret process is running."""
    ui.print_header("Checking prerequisites")

    for path in required_files:
        if not path.exists():
            raise PrerequisiteError(f"Required file not found: '{path}'.")

    ui.print_ok("All required files found.")

    process_name = WINWS_PATH.stem
    if is_process_running(process_name):
        ui.print_warn("Active Zapret process detected.")
        if not ui.ask_yes_no("Terminate it before starting?", default_yes=True):
            raise PrerequisiteError(f"Cannot continue while '{process_name}' is running.")

        if not service_manager.stop():
            raise PrerequisiteError(f"Failed to terminate '{process_name}'. Please close it manually.")

        ui.print_ok("Zapret terminated.")


@dataclass
class StrategyTestResult:
    """Result of a strategy test."""

    success: bool
    avg_time: float = -1.0
    error_msg: str = ""


class StrategyTester:
    """Tests strategies with winws process management."""

    def __init__(self, curl_runner: CurlRunner, winws_manager: WinwsManager):
        self.curl_runner = curl_runner
        self.winws_manager = winws_manager

    def test_strategy(
        self, domains: List[str], strategy: Strategy, test_params: dict, repeats: int, ipset_path: Optional[Path] = None
    ) -> StrategyTestResult:
        """Tests a strategy with winws and Fail-Fast approach."""
        winws_cmd = strategy.build_command(domains, ipset_path)
        total_time = 0
        total_tests = len(domains) * repeats

        with running_winws(self.winws_manager, winws_cmd):
            test_func = partial(self.curl_runner.perform_test, **test_params)

            # Fail-Fast: stop at first failure
            for repeat_idx in range(repeats):
                with ThreadPoolExecutor(max_workers=CURL_MAX_WORKERS) as executor:
                    # Submit all domains for this repeat
                    futures = {executor.submit(test_func, domain=d): d for d in domains}

                    for future in as_completed(futures):
                        result = future.result()

                        if not result.success:
                            # Cancel remaining tasks
                            for f in futures:
                                f.cancel()

                            output = f"Failed on '{result.domain}': {result.output}" if len(domains) > 1 else result.output
                            return StrategyTestResult(False, error_msg=output)

                        total_time += result.time_taken

        avg_time = total_time / total_tests if total_tests > 0 else 0
        return StrategyTestResult(True, avg_time=avg_time)
