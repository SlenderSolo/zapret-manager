import subprocess
import time
import socket
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from .config import *
from . import ui
from .utils import is_process_running, running_winws
from .winws_manager import WinWSManager

class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        if tokens_to_add > 0:
            self.tokens = min(self.capacity, self.tokens + tokens_to_add)
            self.last_refill = now

    def acquire(self, tokens: int = 1) -> bool:
        with self.lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def wait_for_token(self, tokens: int = 1):
        while not self.acquire(tokens):
            with self.lock:
                self._refill()
                required = tokens - self.tokens
                if required > 0:
                    wait_time = required / self.refill_rate
                else:
                    wait_time = 1 / self.refill_rate
            time.sleep(wait_time)

BIN_DIR = BASE_DIR / "bin"
WINWS_PATH = BIN_DIR / "winws.exe"
CURL_PATH = BIN_DIR / "curl.exe"
STRATEGIES_PATH = BIN_DIR / "strategies.txt"

@dataclass
class CurlTestResult:
    success: bool
    return_code: int
    output: str
    time_taken: float = -1.0
    domain: str = ""

@dataclass
class StrategyTestResult:
    success: bool
    avg_time: float = -1.0
    curl_output: str = ""
    winws_stderr: str = ""

@dataclass
class ReportEntry:
    strategy: str
    time: float

class BlockCheckError(Exception):
    pass

class BlockChecker:
    CHECKS_CONFIG = {
        'http': {'title': 'HTTP', 'test_params': {'port': 80}},
        'https_tls12': {'title': 'HTTPS (TLS 1.2)', 'test_params': {'port': 443, 'tls_version': "1.2"}},
        'https_tls13': {'title': 'HTTPS (TLS 1.3)', 'capability': 'tls1.3', 'test_params': {'port': 443, 'tls_version': "1.3"}},
        'http3': {'title': 'HTTP/3 (QUIC)', 'capability': 'http3', 'test_params': {'port': 443, 'http3_only': True}},
    }

    def __init__(self):
        self.domains: List[str] = []
        self.repeats: int = 1
        self.checks_to_run: Dict[str, bool] = {}
        self.curl_caps: Dict[str, bool] = {'tls1.3': False, 'http3': False}
        self.reports: Dict[str, List[ReportEntry]] = {}
        self.initial_accessibility: Dict[str, Dict[str, bool]] = {}
        self.winws_manager = WinWSManager(str(WINWS_PATH), str(BIN_DIR))
        self.strategies_by_test: Dict[str, List[List[str]]] = {}
        self.rate_limiter = TokenBucket(TOKEN_BUCKET_CAPACITY, TOKEN_BUCKET_REFILL_RATE)

    # --- Setup and Configuration ---
    def _check_prerequisites(self):
        ui.print_header("Checking prerequisites")
        required_files = [WINWS_PATH, CURL_PATH, STRATEGIES_PATH]
        for path in required_files:
            if not path.exists():
                raise BlockCheckError(f"Required file not found: '{path}'.")
        ui.print_ok("All required binaries and strategy file found.")
        if is_process_running('winws') or is_process_running('goodbyedpi'):
            ui.print_warn("A DPI bypass process is already running, which may interfere with results.")
            input("Press Enter to continue anyway...")

    def _check_curl_capabilities(self):
        ui.print_header("Checking curl capabilities")
        try:
            res = subprocess.run([CURL_PATH, '-V'], capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR)
            version_output = res.stdout.lower()
            self.curl_caps['tls1.3'] = 'ssl' in version_output
            self.curl_caps['http3'] = 'http3' in version_output
            print(f"TLS 1.3 support: {ui.Fore.GREEN}Yes{ui.Style.RESET_ALL}" if self.curl_caps['tls1.3'] else f"{ui.Fore.RED}No{ui.Style.RESET_ALL}")
            print(f"HTTP/3 support: {ui.Fore.GREEN}Yes{ui.Style.RESET_ALL}" if self.curl_caps['http3'] else f"{ui.Fore.RED}No{ui.Style.RESET_ALL}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise BlockCheckError(f"Failed to check curl capabilities: {e}")

    def ask_params(self):
        ui.print_header("Blockcheck Configuration")
        default_domains = "rutracker.org"
        domains_input = input(f"Enter domain(s) to test, separated by spaces (default: {default_domains}): ")
        self.domains = domains_input.split() if domains_input else [default_domains]
        repeats_input = input("How many times to repeat each test (default: 1): ")
        self.repeats = int(repeats_input) if repeats_input.isdigit() and int(repeats_input) > 0 else 1
        
        for key, config in self.CHECKS_CONFIG.items():
            if config.get('capability') and not self.curl_caps.get(config['capability']):
                self.checks_to_run[key] = False
                ui.print_warn(f"{config['title']} not supported by your curl version, skipping.")
            else:
                self.checks_to_run[key] = ui.ask_yes_no(f"Check {config['title']}?", default_yes=DEFAULT_CHECKS.get(key, True))

    def _load_strategies_from_file(self):
        ui.print_header("Loading strategies from file")
        self.strategies_by_test = {key: [] for key in self.checks_to_run if self.checks_to_run[key]}
        strategy_map = {'https': ['https_tls12', 'https_tls13']}

        try:
            with STRATEGIES_PATH.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or ' : ' not in line: continue
                    test_name_raw, params_raw = line.split(' : ', 1)
                    params_list = params_raw.split()[1:] # Skip the 'winws' part
                    
                    for key in strategy_map.get(test_name_raw.strip(), [test_name_raw.strip()]):
                        if self.checks_to_run.get(key):
                            self.strategies_by_test.setdefault(key, []).append(params_list)
        except FileNotFoundError:
            raise BlockCheckError(f"Strategy file not found at: {STRATEGIES_PATH}")
        
        for test_name, strategies in self.strategies_by_test.items():
            title = self.CHECKS_CONFIG.get(test_name, {}).get('title', test_name.upper())
            ui.print_info(f"Loaded {len(strategies)} strategies for {title}.")

    # --- Test Execution ---
    def _perform_curl_test(self, domain: str, port: int, tls_version: Optional[str] = None, http3_only: bool = False) -> CurlTestResult:
        self.rate_limiter.wait_for_token()
        try:
            ip = socket.gethostbyname(domain)
        except socket.gaierror:
            return CurlTestResult(success=False, return_code=6, output=f"Could not resolve host '{domain}'", domain=domain)

        protocol = "https" if port == 443 else "http"
        url = f"{protocol}://{domain}"
        cmd = [
            str(CURL_PATH), '-sS', '-I', '-A', USER_AGENT, '--max-time', str(CURL_TIMEOUT),
            '--connect-to', f"{domain}:{port}:{ip}:{port}", url
        ]
        if tls_version == "1.2": cmd.extend(['--tlsv1.2', '--tls-max', '1.2'])
        elif tls_version == "1.3": cmd.append('--tlsv1.3')
        if http3_only: cmd.append('--http3-only')

        try:
            start_time = time.perf_counter()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CURL_TIMEOUT + 2, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR)
            end_time = time.perf_counter()
            output = (result.stdout + result.stderr).strip()

            if result.returncode == 0:
                # Check for suspicious redirects
                http_status_line = output.splitlines()[0] if output else ""
                if not REDIRECT_AS_SUCCESS and " 30" in http_status_line and domain not in output.lower():
                    return CurlTestResult(success=False, return_code=254, output="Suspicious redirection", domain=domain)
                return CurlTestResult(success=True, return_code=0, output="Success", time_taken=end_time - start_time, domain=domain)
            
            return CurlTestResult(success=False, return_code=result.returncode, output=output, domain=domain)
        except subprocess.TimeoutExpired:
            return CurlTestResult(success=False, return_code=28, output="Operation timed out", domain=domain)

    def _run_repeated_test(self, domain: str, test_func: Callable[..., CurlTestResult], repeats: int) -> CurlTestResult:
        if repeats == 1:
            return test_func(domain=domain)
        
        fastest_time = float('inf')
        last_result = CurlTestResult(success=False, return_code=-1, output="Test did not run", domain=domain)
        for _ in range(repeats):
            result = test_func(domain=domain)
            if not result.success: return result
            if result.time_taken < fastest_time: fastest_time = result.time_taken
            last_result = result
        
        last_result.time_taken = fastest_time
        return last_result

    def _process_strategy_template(self, template: List[str], domains: List[str]) -> List[str]:
        final_params = []
        
        for param in template:
            if "%~dp0" in param and "=" in param:
                key, value = param.split('=', 1)
                relative_path = value.strip('"').replace("%~dp0", "").lstrip("\/")
                full_path = BASE_DIR / relative_path
                full_path_str = str(full_path)
                if ' ' in full_path_str:
                    final_params.append(f'{key}="{full_path_str}"')
                else:
                    final_params.append(f'{key}={full_path_str}')
            else:
                final_params.append(param)
        final_params.append(f"--hostlist-domains={','.join(domains)}")
        return final_params

    def _test_one_strategy(self, domains_to_test: List[str], template: List[str], test_params: dict) -> StrategyTestResult:
        winws_command = self._process_strategy_template(template, self.domains)
        total_time = 0

        try:
            with running_winws(self.winws_manager, winws_command):
                
                test_func = partial(self._perform_curl_test, **test_params)
                
                with ThreadPoolExecutor(max_workers=CURL_MAX_WORKERS) as executor:
                    repeated_test_partial = partial(self._run_repeated_test, test_func=test_func, repeats=self.repeats)
                    results = executor.map(repeated_test_partial, domains_to_test)
                    for result in results:
                        if not result.success:
                            output = f"Failed on '{result.domain}': {result.output}" if len(domains_to_test) > 1 else result.output
                            return StrategyTestResult(success=False, curl_output=output)
                        total_time += result.time_taken

        except RuntimeError as e:
            return StrategyTestResult(success=False, curl_output=str(e), winws_stderr=self.winws_manager.get_stderr())

        avg_time = total_time / len(domains_to_test) if domains_to_test else 0
        return StrategyTestResult(success=True, avg_time=avg_time)

    def _check_initial_accessibility(self, test_key: str, test_params: dict):
        self.initial_accessibility[test_key] = {}
        ui.print_info("- Checking initial accessibility without DPI bypass...")

        with ThreadPoolExecutor(max_workers=CURL_MAX_WORKERS) as executor:
            test_func = partial(self._perform_curl_test, **test_params)
            repeated_test_partial = partial(self._run_repeated_test, test_func=test_func, repeats=1)
            results = executor.map(repeated_test_partial, self.domains)

            for result in results:
                self.initial_accessibility[test_key][result.domain] = result.success
                status = f" {ui.Fore.GREEN}ACCESSIBLE{ui.Style.RESET_ALL}" if result.success else f" {ui.Fore.RED}BLOCKED{ui.Style.RESET_ALL}"
                print(f"  - {result.domain}: {status}")

        return all(self.initial_accessibility[test_key].values())

    def _run_strategies_for_test(self, test_key: str, test_params: dict):
        domains_to_test = self.domains
        if ONLY_BLOCKED_DOMAINS:
            domains_to_test = [d for d, acc in self.initial_accessibility[test_key].items() if not acc]
            ui.print_info(f"Domains to unblock: {ui.Style.BRIGHT}{', '.join(domains_to_test) or 'None'}{ui.Style.RESET_ALL}")
        if not domains_to_test: return

        strategy_templates = self.strategies_by_test.get(test_key, [])
        ui.print_info(f"\n- Starting tests with {len(strategy_templates)} loaded strategies...")
        for i, template in enumerate(strategy_templates):
            short_name = ' '.join(p for p in template if not p.startswith('--wf-'))
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(strategy_templates)}]{ui.Style.RESET_ALL} Testing: {short_name}")

            result = self._test_one_strategy(domains_to_test, template, test_params)

            if result.success:
                time_label = "Avg Time" if len(domains_to_test) > 1 or self.repeats > 1 else "Time"
                status_msg = f"{ui.Style.BRIGHT+ui.Fore.GREEN}SUCCESS ({time_label}: {result.avg_time:.3f}s){ui.Style.RESET_ALL}"
                self._add_report(test_key, template, result.avg_time)
                print(f"  Result: {status_msg}")
            else:
                if result.winws_stderr:
                    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
                    ui.print_err(f"    WinWS CRASHED: {result.winws_stderr.strip()}")
                elif result.curl_output:
                    status_msg = f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL} - {ui.Fore.YELLOW}{result.curl_output.strip()}{ui.Style.RESET_ALL}"
                    print(status_msg)
                else:
                    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")

    def _run_test_suite(self, test_key: str, test_config: dict):
        ui.print_header(f"Testing {test_config['title'].upper()} for domains: {', '.join(self.domains)}")
        if self._check_initial_accessibility(test_key, test_config['test_params']):
            ui.print_info("All sites are initially accessible, skipping bypass tests for this protocol.")
            return
        self._run_strategies_for_test(test_key, test_config['test_params'])

    # --- Reporting ---
    def _add_report(self, test_key: str, strategy_template: List[str], time_taken: float):
        if test_key not in self.reports: self.reports[test_key] = []
        self.reports[test_key].append(ReportEntry(strategy=' '.join(strategy_template), time=time_taken))

    def _generate_summary(self) -> str:
        summary_lines = [f"SUMMARY for {', '.join(self.domains)}", "=" * (8 + len(', '.join(self.domains))) + "\n"]
        time_label = "Avg Time" if len(self.domains) > 1 or self.repeats > 1 else "Time"

        for key, config in self.CHECKS_CONFIG.items():
            results = sorted(self.reports.get(key, []), key=lambda r: r.time)
            if not results: continue
            
            summary_lines.append(f"# Successful {config['title']} strategies (sorted by speed):")
            for res in results:
                display_strategy = ' '.join(p for p in res.strategy.split() if not p.startswith('--wf-'))
                summary_lines.append(f"  ({time_label}: {res.time:.3f}s) {display_strategy}")
            summary_lines.append("")
        return "\n".join(summary_lines)

    def print_summary(self):
        ui.print_header(f"SUMMARY for {', '.join(self.domains)}")
        if not any(self.reports.values()):
            ui.print_warn(f"No working strategies found for the given domains.")
            return

        summary_content = self._generate_summary()
        console_output = summary_content.replace("# ", ui.Style.BRIGHT + ui.Fore.GREEN)
        console_output = console_output.replace("strategies", f"strategies{ui.Style.RESET_ALL}")
        print(console_output)

        result_file_path = BASE_DIR / "result.txt"
        try:
            result_file_path.write_text(summary_content, encoding='utf-8')
            ui.print_ok(f"\nSummary report saved to: {result_file_path}")
        except OSError as e:
            ui.print_err(f"Failed to save summary report: {e}")

    # --- Main Execution ---
    def run_all_tests(self):
        self._load_strategies_from_file()
        self.reports.clear()
        self.initial_accessibility.clear()
        for key, config in self.CHECKS_CONFIG.items():
            if self.checks_to_run.get(key):
                self._run_test_suite(key, config)
        self.print_summary()

    def cleanup(self):
        ui.print_info("\nCleaning up...")
        self.winws_manager.stop()
