import os
import subprocess
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from ..config import *
from .. import ui
from .network_utils import DNSCache, CurlRunner, CurlTestResult
from ..utils import is_process_running, running_winws, TokenBucket
from .winws_manager import WinWSManager
from .strategy import Strategy, StrategyManager

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
        self.strategy_manager = StrategyManager(STRATEGIES_PATH)
        self.test_mode: str = 'domain'  # 'domain' or 'ipset'
        self.ipset_path: Optional[Path] = None

        # Initialize network utilities
        self.dns_cache = DNSCache(ttl=DNS_CACHE_TTL)
        self.rate_limiter = TokenBucket(TOKEN_BUCKET_CAPACITY, TOKEN_BUCKET_REFILL_RATE)
        self.curl_runner = CurlRunner(self.dns_cache, self.rate_limiter)

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

    def _get_ipsets(self) -> List[Path]:
        if not LISTS_DIR.exists():
            return []
        return sorted([f for f in LISTS_DIR.glob('ipset*.txt')])

    def ask_params(self):
        ui.print_header("Blockcheck Configuration")

        if self.test_mode == 'domain':
            default_domains = "rutracker.org"
            domains_input = input(f"Enter domain(s) to test, separated by spaces (default: {default_domains}): ")
            self.domains = domains_input.split() if domains_input else [default_domains]
        elif self.test_mode == 'ipset':
            ipsets = self._get_ipsets()
            if not ipsets:
                raise BlockCheckError("No ipset files found in the 'lists' directory.")
            
            ipset_filenames = [os.path.basename(p) for p in ipsets]
            selected_ipset_filename = ui.ask_choice("Select IPSet to test:", ipset_filenames)
            if selected_ipset_filename:
                self.ipset_path = LISTS_DIR / selected_ipset_filename
                default_domains = "stryker.com"
                domains_input = input(f"Enter domain(s) to test, separated by spaces (default: {default_domains}): ")
                self.domains = domains_input.split() if domains_input else [default_domains]
            else:
                raise BlockCheckError("No IPSet selected.")

        repeats_input = input("How many times to repeat each test (default: 1): ")
        self.repeats = int(repeats_input) if repeats_input.isdigit() and int(repeats_input) > 0 else 1
        
        for key, config in self.CHECKS_CONFIG.items():
            if config.get('capability') and not self.curl_caps.get(config['capability']):
                self.checks_to_run[key] = False
                ui.print_warn(f"{config['title']} not supported by your curl version, skipping.")
            else:
                self.checks_to_run[key] = ui.ask_yes_no(f"Check {config['title']}?", default_yes=DEFAULT_CHECKS.get(key, True))

    def _load_strategies(self):
        ui.print_header("Loading strategies")
        self.strategy_manager.load_strategies()
        for key, config in self.CHECKS_CONFIG.items():
            if self.checks_to_run.get(key):
                strategies = self.strategy_manager.get_strategies_for_test(key)
                ui.print_info(f"Loaded {len(strategies)} strategies for {config['title']}.")

    # --- Test Execution ---
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

    def _test_one_strategy(self, domains_to_test: List[str], strategy: Strategy, test_params: dict) -> StrategyTestResult:
        winws_command = strategy.build_command(
            domains=self.domains,
            ipset_path=self.ipset_path if self.test_mode == 'ipset' else None
        )
        total_time = 0

        try:
            with running_winws(self.winws_manager, winws_command):
                
                test_func = partial(self.curl_runner.perform_test, **test_params)
                
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
            test_func = partial(self.curl_runner.perform_test, **test_params)
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

        strategies = self.strategy_manager.get_strategies_for_test(test_key)
        ui.print_info(f"\n- Starting tests with {len(strategies)} loaded strategies...")
        for i, strategy in enumerate(strategies):
            # We no longer need to process the template here, just build for logging
            winws_command_for_log = strategy.build_command(domains_to_test, self.ipset_path if self.test_mode == 'ipset' else None)
            full_command_str = ' '.join(winws_command_for_log)
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(strategies)}]{ui.Style.RESET_ALL} Testing: {strategy.name}")

            result = self._test_one_strategy(domains_to_test, strategy, test_params)

            if result.success:
                time_label = "Avg Time" if len(domains_to_test) > 1 or self.repeats > 1 else "Time"
                status_msg = f"{ui.Style.BRIGHT+ui.Fore.GREEN}SUCCESS ({time_label}: {result.avg_time:.3f}s){ui.Style.RESET_ALL}"
                self._add_report(test_key, strategy, result.avg_time)
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
        if self.test_mode == 'domain':
            ui.print_header(f"Testing {test_config['title'].upper()} for domains: {', '.join(self.domains)}")
            if self._check_initial_accessibility(test_key, test_config['test_params']):
                ui.print_info("All sites are initially accessible, skipping bypass tests for this protocol.")
                return
        else:
            ui.print_header(f"Testing {test_config['title'].upper()} for ipset: {self.ipset_path.name}")
        
        self._run_strategies_for_test(test_key, test_config['test_params'])

    # --- Reporting ---
    def _add_report(self, test_key: str, strategy: Strategy, time_taken: float):
        if test_key not in self.reports: self.reports[test_key] = []
        self.reports[test_key].append(ReportEntry(strategy=strategy.name, time=time_taken))

    def _generate_summary(self) -> str:
        if self.test_mode == 'domain':
            summary_title = f"SUMMARY for {', '.join(self.domains)}"
        else:
            summary_title = f"SUMMARY for IPSet: {self.ipset_path.name}"
        summary_lines = [summary_title, "=" * len(summary_title) + "\n"]
        time_label = "Avg Time" if (self.test_mode == 'domain' and len(self.domains) > 1) or self.repeats > 1 else "Time"

        for key, config in self.CHECKS_CONFIG.items():
            results = sorted(self.reports.get(key, []), key=lambda r: r.time)
            if not results: continue
            
            summary_lines.append(f"# Successful {config['title']} strategies (sorted by speed):")
            for res in results:
                display_strategy = res.strategy
                summary_lines.append(f"  ({time_label}: {res.time:.3f}s) {display_strategy}")
            summary_lines.append("")
        return "\n".join(summary_lines)

    def print_summary(self):
        if self.test_mode == 'domain':
            ui.print_header(f"SUMMARY for {', '.join(self.domains)}")
            if not any(self.reports.values()):
                ui.print_warn(f"No working strategies found for the given domains.")
                return
        else:
            ui.print_header(f"SUMMARY for IPSet: {self.ipset_path.name}")
            if not any(self.reports.values()):
                ui.print_warn(f"No working strategies found for the given ipset.")
                return

        summary_content = self._generate_summary()
        console_output = summary_content.replace("# ", ui.Style.BRIGHT + ui.Fore.GREEN)
        console_output = console_output.replace("strategies", f"strategies{ui.Style.RESET_ALL}")
        print(console_output)

        stats = self.dns_cache.get_stats()
        total_lookups = stats['hits'] + stats['misses']
        hit_rate = (stats['hits'] / total_lookups * 100) if total_lookups > 0 else 0
        if total_lookups > 0:
            ui.print_info(f"DNS Cache Stats: {stats['hits']} hits, {stats['misses']} misses ({hit_rate:.1f}% hit rate)")

        result_file_path = BASE_DIR / "result.txt"
        try:
            result_file_path.write_text(summary_content, encoding='utf-8')
            ui.print_ok(f"\nSummary report saved to: {result_file_path}")
        except OSError as e:
            ui.print_err(f"Failed to save summary report: {e}")

    # --- Main Execution ---
    def run_all_tests(self):
        self._load_strategies()
        self.reports.clear()
        self.initial_accessibility.clear()
        for key, config in self.CHECKS_CONFIG.items():
            if self.checks_to_run.get(key):
                self._run_test_suite(key, config)
        self.print_summary()

    def cleanup(self):
        ui.print_info("\nCleaning up...")
        self.winws_manager.stop()