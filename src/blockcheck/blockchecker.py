import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

from config import *
from src import ui
from .network_utils import DNSCache, CurlRunner
from src.utils import is_process_running, running_winws, TokenBucket, kill_process
from .winws_manager import WinWSManager
from .strategy import Strategy, StrategyManager
from .domain_preset_parser import DomainPresetParser


class BlockCheckError(Exception):
    """Custom exception for BlockChecker errors."""
    pass


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class TestConfiguration:
    """Holds all test parameters."""
    
    domains: List[str] = field(default_factory=list)
    ipset_path: Optional[Path] = None
    repeats: int = 1
    checks_to_run: Dict[str, bool] = field(default_factory=dict)
    test_mode: str = 'domain'  # 'domain' or 'ipset'
    
    CHECKS_CONFIG = {
        'http': {'title': 'HTTP', 'test_params': {'port': 80}},
        'https_tls12': {'title': 'HTTPS (TLS 1.2)', 'capability': 'tls1.3', 'test_params': {'port': 443, 'tls_version': "1.2"}},
        'https_tls13': {'title': 'HTTPS (TLS 1.3)', 'capability': 'tls1.3', 'test_params': {'port': 443, 'tls_version': "1.3"}},
        'http3': {'title': 'HTTP/3 (QUIC)', 'capability': 'http3', 'test_params': {'port': 443, 'http3_only': True}},
    }
    
    def __post_init__(self):
        if not self.checks_to_run:
            self.checks_to_run = DEFAULT_CHECKS.copy()
    
    def configure_from_user(self, curl_capabilities: Dict[str, bool], preset_parser: DomainPresetParser):
        """Interactive configuration."""
        ui.print_header("Blockcheck Configuration")
        
        if self.test_mode == 'domain':
            self._select_domain_preset(preset_parser, 'domain')
        elif self.test_mode == 'ipset':
            self._select_ipset_file()
            self._select_domain_preset(preset_parser, 'ipset')
        
        repeats_input = input("How many times to repeat each test (default: 1): ")
        self.repeats = int(repeats_input) if repeats_input.isdigit() and int(repeats_input) > 0 else 1
        
        for key, config in self.CHECKS_CONFIG.items():
            required_cap = config.get('capability')
            if required_cap and not curl_capabilities.get(required_cap):
                self.checks_to_run[key] = False
                ui.print_warn(f"{config['title']} not supported by your curl version, skipping.")
            else:
                self.checks_to_run[key] = ui.ask_yes_no(f"Check {config['title']}?", default_yes=DEFAULT_CHECKS.get(key, True))
    
    def _select_ipset_file(self):
        ipsets = sorted([f for f in LISTS_DIR.glob('ipset*.txt')]) if LISTS_DIR.exists() else []
        if not ipsets:
            raise BlockCheckError("No ipset files found in the 'lists' directory.")
        
        selected = ui.ask_choice("Select IPSet to test:", [p.name for p in ipsets])
        if not selected:
            raise BlockCheckError("No IPSet selected.")
        self.ipset_path = LISTS_DIR / selected
    
    def _select_domain_preset(self, preset_parser: DomainPresetParser, mode: str):
        presets = preset_parser.get_presets_for_mode(mode)
        selected_name = ui.ask_choice("Select domain preset:", [p.name for p in presets])
        if not selected_name:
            raise BlockCheckError("No preset selected.")
        
        selected_preset = preset_parser.get_preset_by_name(mode, selected_name)
        if not selected_preset:
            raise BlockCheckError(f"Preset '{selected_name}' not found.")
        
        if selected_preset.name == "Custom":
            default_domain = DEFAULT_DOMAIN if mode == 'domain' else DEFAULT_IPSET_DOMAIN
            inp = input(f"Enter domain(s) to test, separated by spaces (default: {default_domain}): ")
            self.domains = inp.split() if inp else [default_domain]
        else:
            self.domains = selected_preset.domains
            ui.print_info(f"Using preset '{selected_preset.name}' with domains: {', '.join(self.domains)}")
    
    def get_enabled_checks(self) -> List[str]:
        return [key for key, enabled in self.checks_to_run.items() if enabled]


# ============================================================================
# Strategy Testing
# ============================================================================

@dataclass
class StrategyTestResult:
    """Result of a strategy test."""
    success: bool
    avg_time: float = -1.0
    curl_output: str = ""
    winws_stderr: str = ""


class StrategyTester:
    """Tests strategies with WinWS process management."""
    
    def __init__(self, curl_runner: CurlRunner, winws_manager: WinWSManager):
        self.curl_runner = curl_runner
        self.winws_manager = winws_manager
    
    def test_strategy(self, domains: List[str], strategy: Strategy, 
                     test_params: dict, repeats: int, 
                     ipset_path: Optional[Path] = None) -> StrategyTestResult:
        """Tests a strategy by building and executing command."""
        winws_cmd = strategy.build_command(domains, ipset_path)
        return self._test_with_command(domains, winws_cmd, test_params, repeats)
    
    def test_raw_command(self, domains: List[str], winws_cmd: List[str],
                        test_params: dict, repeats: int = 1) -> StrategyTestResult:
        """Tests with pre-built winws command (for optimizer)."""
        return self._test_with_command(domains, winws_cmd, test_params, repeats)
    
    def _test_with_command(self, domains: List[str], winws_cmd: List[str],
                          test_params: dict, repeats: int) -> StrategyTestResult:
        """Core testing logic with WinWS and Fail-Fast approach."""
        total_time = 0
        total_tests = len(domains) * repeats
        
        try:
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
                                
                                output = (f"Failed on '{result.domain}': {result.output}" 
                                        if len(domains) > 1 else result.output)
                                return StrategyTestResult(False, curl_output=output)
                            
                            total_time += result.time_taken
                
        except RuntimeError as e:
            return StrategyTestResult(False, curl_output=str(e), 
                                    winws_stderr=self.winws_manager.get_stderr())
        
        avg_time = total_time / total_tests if total_tests > 0 else 0
        return StrategyTestResult(True, avg_time=avg_time)


# ============================================================================
# Main BlockChecker
# ============================================================================

class BlockChecker:
    """
    Main DPI bypass testing coordinator.
    Orchestrates configuration, accessibility checks, strategy testing, and reporting.
    """
    
    def __init__(self):
        # Configuration
        self.config = TestConfiguration()
        self.curl_caps: Dict[str, bool] = {'tls1.3': False, 'http3': False}
        
        # Managers
        self.preset_parser = DomainPresetParser(DOMAIN_PRESETS_PATH)
        self.strategy_manager = StrategyManager(STRATEGIES_PATH)
        self.winws_manager = WinWSManager(str(WINWS_PATH), str(BIN_DIR))
        
        # Network infrastructure
        self.dns_cache = DNSCache(ttl=DNS_CACHE_TTL)
        self.rate_limiter = TokenBucket(TOKEN_BUCKET_CAPACITY, TOKEN_BUCKET_REFILL_RATE)
        self.curl_runner = CurlRunner(self.dns_cache, self.rate_limiter)
        
        # Testing
        self.strategy_tester = StrategyTester(self.curl_runner, self.winws_manager)
        self.reporter = ui.TestReporter()
        
        # Accessibility tracking
        self.initial_accessibility: Dict[str, Dict[str, bool]] = {}
    
    # === Setup ===
    
    def check_prerequisites(self):
            """Validates required files and binaries."""
            ui.print_header("Checking prerequisites")

            for path in [WINWS_PATH, CURL_PATH, STRATEGIES_PATH]:
                if not path.exists():
                    raise BlockCheckError(f"Required file not found: '{path}'.")
            ui.print_ok("All required binaries and strategy file found.")

            if is_process_running('winws'):
                ui.print_warn("Active Zapret process detected.")
                if ui.ask_yes_no("Terminate it before starting?", default_yes=True):
                    if kill_process('winws'):
                        ui.print_ok("Zapret terminated.")
                    else:
                        ui.print_err("Failed to terminate 'winws'. Please close it manually.")
    
    def check_curl_capabilities(self):
        """Detects curl capabilities."""
        ui.print_header("Checking curl capabilities")
        try:
            result = subprocess.run(
                [CURL_PATH, '-V'], capture_output=True, text=True, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR
            )
            version_output = result.stdout.lower()
            self.curl_caps['tls1.3'] = 'ssl' in version_output
            self.curl_caps['http3'] = 'http3' in version_output
            
            for cap, supported in self.curl_caps.items():
                color = ui.Fore.GREEN if supported else ui.Fore.RED
                status = "Yes" if supported else "No"
                label = "TLS 1.3" if cap == 'tls1.3' else "HTTP/3"
                print(f"{label}: {color}{status}{ui.Style.RESET_ALL}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise BlockCheckError(f"Failed to check curl capabilities: {e}")
    
    def configure_test(self, test_mode: str = 'domain'):
        """Interactive test configuration."""
        self.config.test_mode = test_mode
        self.config.configure_from_user(self.curl_caps, self.preset_parser)
    
    def load_strategies(self):
        """Loads DPI bypass strategies."""
        ui.print_header("Loading strategies")
        self.strategy_manager.load_strategies()
        
        for key in self.config.get_enabled_checks():
            config = self.config.CHECKS_CONFIG[key]
            strategies = self.strategy_manager.get_strategies_for_test(key)
            ui.print_info(f"Loaded {len(strategies)} strategies for {config['title']}.")
    
    # === Accessibility Checking ===
    
    def check_accessibility(self, test_key: str, test_params: dict) -> bool:
        """Checks initial domain accessibility without DPI bypass."""
        ui.print_info("- Checking initial accessibility without DPI bypass...")
        self.initial_accessibility[test_key] = {}
        
        with ThreadPoolExecutor(max_workers=CURL_MAX_WORKERS) as executor:
            test_func = partial(self.curl_runner.perform_test, **test_params)
            
            for result in executor.map(lambda d: test_func(domain=d), self.config.domains):
                self.initial_accessibility[test_key][result.domain] = result.success
                status = (f"{ui.Fore.GREEN}ACCESSIBLE{ui.Style.RESET_ALL}" 
                         if result.success else f"{ui.Fore.RED}BLOCKED{ui.Style.RESET_ALL}")
                print(f"  - {result.domain}: {status}")
        
        return all(self.initial_accessibility[test_key].values())
    
    def get_domains_to_test(self, test_key: str) -> List[str]:
        """Returns domains that need testing based on configuration."""
        if not ONLY_BLOCKED_DOMAINS:
            return self.config.domains
        
        if test_key not in self.initial_accessibility:
            return self.config.domains
        
        blocked = [d for d, accessible in self.initial_accessibility[test_key].items() if not accessible]
        
        if blocked:
            ui.print_info(f"Domains to unblock: {ui.Style.BRIGHT}{', '.join(blocked)}{ui.Style.RESET_ALL}")
        else:
            ui.print_info("No blocked domains found.")
        
        return blocked
    
    # === Testing ===
    
    def run_test_suite(self, test_key: str):
        """Runs complete test suite for one protocol."""
        config = self.config.CHECKS_CONFIG[test_key]
        test_params = config['test_params']
        
        # Print header
        if self.config.test_mode == 'domain':
            domains_str = ', '.join(self.config.domains)
            ui.print_header(f"Testing {config['title'].upper()} for domains: {domains_str}")
            
            if self.check_accessibility(test_key, test_params):
                ui.print_info("All sites are initially accessible, skipping bypass tests for this protocol.")
                return
            
            domains_to_test = self.get_domains_to_test(test_key)
        else:
            ui.print_header(f"Testing {config['title'].upper()} for ipset: {self.config.ipset_path.name}")
            domains_to_test = self.config.domains
        
        if not domains_to_test:
            return
        
        # Test all strategies
        strategies = self.strategy_manager.get_strategies_for_test(test_key)
        ui.print_info(f"\n- Starting tests with {len(strategies)} loaded strategies...")
        
        for i, strategy in enumerate(strategies):
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(strategies)}]{ui.Style.RESET_ALL} Testing: {strategy.name}")
            
            result = self.strategy_tester.test_strategy(
                domains_to_test, strategy, test_params, self.config.repeats,
                self.config.ipset_path if self.config.test_mode == 'ipset' else None
            )
            
            if result.success:
                label = "Avg Time" if len(domains_to_test) > 1 or self.config.repeats > 1 else "Time"
                print(f"  Result: {ui.Style.BRIGHT+ui.Fore.GREEN}SUCCESS ({label}: {result.avg_time:.3f}s){ui.Style.RESET_ALL}")
                self.reporter.add_result(test_key, strategy.name, result.avg_time)
            else:
                if result.winws_stderr:
                    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL} - {ui.Fore.YELLOW}{result.curl_output.strip()}{ui.Style.RESET_ALL}")
                else:
                    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL} - "
                          f"{ui.Fore.YELLOW}{result.curl_output.strip()}{ui.Style.RESET_ALL}")
    
    # === Main Execution ===
    
    def run_all_tests(self):
        """Executes all enabled tests and generates report."""
        self.load_strategies()
        self.reporter.clear()
        self.initial_accessibility.clear()
        
        for test_key in self.config.get_enabled_checks():
            self.run_test_suite(test_key)
        
        # Generate report
        if self.config.test_mode == 'domain':
            title = f"SUMMARY for {', '.join(self.config.domains)}"
        else:
            title = f"SUMMARY for IPSet: {self.config.ipset_path.name}"
        
        self.reporter.print_and_save(
            test_title=title,
            checks_config=self.config.CHECKS_CONFIG,
            multiple_domains=len(self.config.domains) > 1,
            repeats=self.config.repeats,
            dns_stats=self.dns_cache.get_stats(),
            save_path=BASE_DIR / "result.txt"
        )
    
    def cleanup(self):
        """Cleanup resources."""
        ui.print_info("\nCleaning up...")
        self.winws_manager.stop()
