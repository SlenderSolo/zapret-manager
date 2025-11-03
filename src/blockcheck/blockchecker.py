import os
import subprocess
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

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


class StrategyTester:
    """
    Tests strategies with WinWS.
    Critical: isolates complex logic of process launching + parallel tests.
    Reused in preset_optimizer.py
    """
    
    def __init__(self, curl_runner: CurlRunner, winws_manager: WinWSManager):
        self.curl_runner = curl_runner
        self.winws_manager = winws_manager
    
    def run_repeated_test(self, domain: str, test_func: Callable, repeats: int) -> CurlTestResult:
        """Runs test multiple times, returns fastest successful result."""
        if repeats == 1:
            return test_func(domain=domain)
        
        fastest, last = float('inf'), CurlTestResult(False, -1, "Test did not run", domain=domain)
        for _ in range(repeats):
            result = test_func(domain=domain)
            if not result.success:
                return result
            if result.time_taken < fastest:
                fastest = result.time_taken
            last = result
        
        last.time_taken = fastest
        return last
    
    def test_strategy(self, domains: List[str], strategy: Strategy, 
                     test_params: dict, repeats: int, 
                     ipset_path: Optional[Path] = None) -> StrategyTestResult:
        """Tests a strategy object (builds command via Strategy.build_command)."""
        winws_cmd = strategy.build_command(domains, ipset_path)
        return self._test_with_command(domains, winws_cmd, test_params, repeats)
    
    def test_raw_command(self, domains: List[str], winws_cmd: List[str],
                        test_params: dict, repeats: int = 1) -> StrategyTestResult:
        """
        Tests with a pre-built winws command (for preset optimizer).
        Unlike test_strategy(), this doesn't call Strategy.build_command().
        """
        return self._test_with_command(domains, winws_cmd, test_params, repeats)
    
    def _test_with_command(self, domains: List[str], winws_cmd: List[str],
                          test_params: dict, repeats: int) -> StrategyTestResult:
        """Internal method that performs actual testing with given command."""
        total_time = 0

        try:
            with running_winws(self.winws_manager, winws_cmd):
                test_func = partial(self.curl_runner.perform_test, **test_params)
                
                with ThreadPoolExecutor(max_workers=CURL_MAX_WORKERS) as executor:
                    repeated_test = partial(self.run_repeated_test, test_func=test_func, repeats=repeats)
                    
                    for result in executor.map(repeated_test, domains):
                        if not result.success:
                            output = f"Failed on '{result.domain}': {result.output}" if len(domains) > 1 else result.output
                            return StrategyTestResult(False, curl_output=output)
                        total_time += result.time_taken
                        
        except RuntimeError as e:
            return StrategyTestResult(False, curl_output=str(e), winws_stderr=self.winws_manager.get_stderr())

        return StrategyTestResult(True, avg_time=total_time / len(domains) if domains else 0)


class BlockChecker:
    """Main class for checking and bypassing blocks."""
    
    CHECKS_CONFIG = {
        'http': {'title': 'HTTP', 'test_params': {'port': 80}},
        'https_tls12': {'title': 'HTTPS (TLS 1.2)', 'capability': 'tls1.3', 'test_params': {'port': 443, 'tls_version': "1.2"}},
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
        self.test_mode: str = 'domain'
        self.ipset_path: Optional[Path] = None

        # Managers
        self.winws_manager = WinWSManager(str(WINWS_PATH), str(BIN_DIR))
        self.strategy_manager = StrategyManager(STRATEGIES_PATH)
        
        # Network utilities
        self.dns_cache = DNSCache(ttl=DNS_CACHE_TTL)
        self.rate_limiter = TokenBucket(TOKEN_BUCKET_CAPACITY, TOKEN_BUCKET_REFILL_RATE)
        self.curl_runner = CurlRunner(self.dns_cache, self.rate_limiter)
        
        # Strategy tester (also used in preset_optimizer)
        self.strategy_tester = StrategyTester(self.curl_runner, self.winws_manager)

    # --- Setup and Configuration ---
    
    def _check_prerequisites(self):
        ui.print_header("Checking prerequisites")
        for path in [WINWS_PATH, CURL_PATH, STRATEGIES_PATH]:
            if not path.exists():
                raise BlockCheckError(f"Required file not found: '{path}'.")
        ui.print_ok("All required binaries and strategy file found.")
        
        if is_process_running('winws') or is_process_running('goodbyedpi'):
            ui.print_warn("A DPI bypass process is already running, which may interfere with results.")
            input("Press Enter to continue anyway...")

    def _check_curl_capabilities(self):
        ui.print_header("Checking curl capabilities")
        try:
            res = subprocess.run([CURL_PATH, '-V'], capture_output=True, text=True, check=True, 
                               creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR)
            version_output = res.stdout.lower()
            self.curl_caps['tls1.3'] = 'ssl' in version_output
            self.curl_caps['http3'] = 'http3' in version_output
            
            for cap, supported in self.curl_caps.items():
                color = ui.Fore.GREEN if supported else ui.Fore.RED
                status = "Yes" if supported else "No"
                label = "TLS 1.3" if cap == 'tls1.3' else "HTTP/3"
                print(f"{label}: {color}{status}{ui.Style.RESET_ALL}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise BlockCheckError(f"Failed to check curl capabilities: {e}")

    def ask_params(self):
        ui.print_header("Blockcheck Configuration")

        # Domain/IPSet selection
        if self.test_mode == 'domain':
            inp = input(f"Enter domain(s) to test, separated by spaces (default: {DEFAULT_DOMAIN}): ")
            self.domains = inp.split() if inp else [DEFAULT_DOMAIN]
        elif self.test_mode == 'ipset':
            ipsets = sorted([f for f in LISTS_DIR.glob('ipset*.txt')]) if LISTS_DIR.exists() else []
            if not ipsets:
                raise BlockCheckError("No ipset files found in the 'lists' directory.")

            selected = ui.ask_choice("Select IPSet to test:", [os.path.basename(p) for p in ipsets])
            if not selected:
                raise BlockCheckError("No IPSet selected.")

            self.ipset_path = LISTS_DIR / selected
            inp = input(f"Enter domain(s) to test, separated by spaces (default: {DEFAULT_IPSET_DOMAIN}): ")
            self.domains = inp.split() if inp else [DEFAULT_IPSET_DOMAIN]

        # Repeat count
        repeats_input = input("How many times to repeat each test (default: 1): ")
        self.repeats = int(repeats_input) if repeats_input.isdigit() and int(repeats_input) > 0 else 1
        
        # Test selection
        for key, config in self.CHECKS_CONFIG.items():
            if config.get('capability') and not self.curl_caps.get(config['capability']):
                self.checks_to_run[key] = False
                ui.print_warn(f"{config['title']} not supported by your curl version, skipping.")
            else:
                self.checks_to_run[key] = ui.ask_yes_no(
                    f"Check {config['title']}?", 
                    default_yes=DEFAULT_CHECKS.get(key, True)
                )

    def _load_strategies(self):
        ui.print_header("Loading strategies")
        self.strategy_manager.load_strategies()
        for key, config in self.CHECKS_CONFIG.items():
            if self.checks_to_run.get(key):
                strategies = self.strategy_manager.get_strategies_for_test(key)
                ui.print_info(f"Loaded {len(strategies)} strategies for {config['title']}.")

    # --- Test Execution ---
    
    def _check_accessibility(self, test_key: str, test_params: dict) -> bool:
        """Checks initial domain accessibility without DPI bypass."""
        self.initial_accessibility[test_key] = {}
        ui.print_info("- Checking initial accessibility without DPI bypass...")

        with ThreadPoolExecutor(max_workers=CURL_MAX_WORKERS) as executor:
            test_func = partial(self.curl_runner.perform_test, **test_params)
            
            for result in executor.map(lambda d: test_func(domain=d), self.domains):
                self.initial_accessibility[test_key][result.domain] = result.success
                status = f"{ui.Fore.GREEN}ACCESSIBLE{ui.Style.RESET_ALL}" if result.success else f"{ui.Fore.RED}BLOCKED{ui.Style.RESET_ALL}"
                print(f"  - {result.domain}: {status}")

        return all(self.initial_accessibility[test_key].values())

    def _run_strategies(self, test_key: str, test_params: dict):
        """Runs testing of all strategies for given protocol."""
        # Determine which domains to test
        domains_to_test = self.domains
        if ONLY_BLOCKED_DOMAINS:
            domains_to_test = [d for d, acc in self.initial_accessibility[test_key].items() if not acc]
            ui.print_info(f"Domains to unblock: {ui.Style.BRIGHT}{', '.join(domains_to_test) or 'None'}{ui.Style.RESET_ALL}")
        
        if not domains_to_test:
            return

        strategies = self.strategy_manager.get_strategies_for_test(test_key)
        ui.print_info(f"\n- Starting tests with {len(strategies)} loaded strategies...")
        
        for i, strategy in enumerate(strategies):
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(strategies)}]{ui.Style.RESET_ALL} Testing: {strategy.name}")
            
            # Use StrategyTester to isolate logic
            result = self.strategy_tester.test_strategy(
                domains_to_test, strategy, test_params, self.repeats,
                self.ipset_path if self.test_mode == 'ipset' else None
            )

            if result.success:
                label = "Avg Time" if len(domains_to_test) > 1 or self.repeats > 1 else "Time"
                print(f"  Result: {ui.Style.BRIGHT+ui.Fore.GREEN}SUCCESS ({label}: {result.avg_time:.3f}s){ui.Style.RESET_ALL}")
                
                if test_key not in self.reports:
                    self.reports[test_key] = []
                self.reports[test_key].append(ReportEntry(strategy.name, result.avg_time))
            else:
                if result.winws_stderr:
                    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
                    ui.print_err(f"    WinWS CRASHED: {result.winws_stderr.strip()}")
                else:
                    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL} - {ui.Fore.YELLOW}{result.curl_output.strip()}{ui.Style.RESET_ALL}")

    def _run_test_suite(self, test_key: str, config: dict):
        """Runs full test suite for one protocol."""
        if self.test_mode == 'domain':
            ui.print_header(f"Testing {config['title'].upper()} for domains: {', '.join(self.domains)}")
            if self._check_accessibility(test_key, config['test_params']):
                ui.print_info("All sites are initially accessible, skipping bypass tests for this protocol.")
                return
        else:
            ui.print_header(f"Testing {config['title'].upper()} for ipset: {self.ipset_path.name}")
        
        self._run_strategies(test_key, config['test_params'])

    # --- Reporting ---
    
    def _generate_summary(self) -> str:
        """Generates text report."""
        title = f"SUMMARY for {', '.join(self.domains)}" if self.test_mode == 'domain' else f"SUMMARY for IPSet: {self.ipset_path.name}"
        lines = [title, "=" * len(title) + "\n"]
        label = "Avg Time" if (self.test_mode == 'domain' and len(self.domains) > 1) or self.repeats > 1 else "Time"

        for key, config in self.CHECKS_CONFIG.items():
            results = sorted(self.reports.get(key, []), key=lambda r: r.time)
            if results:
                lines.append(f"# Successful {config['title']} strategies (sorted by speed):")
                lines.extend(f"  ({label}: {r.time:.3f}s) {r.strategy}" for r in results)
                lines.append("")
        
        return "\n".join(lines)

    def print_summary(self):
        """Outputs and saves final report."""
        title = f"SUMMARY for {', '.join(self.domains)}" if self.test_mode == 'domain' else f"SUMMARY for IPSet: {self.ipset_path.name}"
        ui.print_header(title)
        
        if not any(self.reports.values()):
            target = "domains" if self.test_mode == 'domain' else "ipset"
            ui.print_warn(f"No working strategies found for the given {target}.")
            return

        summary = self._generate_summary()
        console = summary.replace("# ", ui.Style.BRIGHT + ui.Fore.GREEN).replace("strategies", f"strategies{ui.Style.RESET_ALL}")
        print(console)

        # DNS stats
        stats = self.dns_cache.get_stats()
        total = stats['hits'] + stats['misses']
        if total > 0:
            ui.print_info(f"DNS Cache Stats: {stats['hits']} hits, {stats['misses']} misses ({stats['hits']/total*100:.1f}% hit rate)")

        # Save to file
        try:
            (BASE_DIR / "result.txt").write_text(summary, encoding='utf-8')
            ui.print_ok(f"\nSummary report saved to: {BASE_DIR / 'result.txt'}")
        except OSError as e:
            ui.print_err(f"Failed to save summary report: {e}")

    # --- Main Execution ---
    
    def run_all_tests(self):
        """Runs all selected tests."""
        self._load_strategies()
        self.reports.clear()
        self.initial_accessibility.clear()
        
        for key, config in self.CHECKS_CONFIG.items():
            if self.checks_to_run.get(key):
                self._run_test_suite(key, config)
        
        self.print_summary()

    def cleanup(self):
        """Resource cleanup."""
        ui.print_info("\nCleaning up...")
        self.winws_manager.stop()