import subprocess
import time
import socket
from pathlib import Path

from .config import *
from . import ui
from .utils import is_process_running
from .winws_manager import WinWSManager

BIN_DIR = BASE_DIR / "bin"
WINWS_PATH = BIN_DIR / "winws.exe"
CURL_PATH = BIN_DIR / "curl.exe"
STRATEGIES_PATH = BIN_DIR / "strategies.txt"

# Custom exception for clean exits.
class BlockCheckError(Exception):
    pass

class BlockChecker:
    def __init__(self):
        self.domains = []
        self.repeats = 1
        self.checks_to_run = {} 
        self.curl_caps = {'tls1.3': False, 'http3': False}
        self.reports = {}
        self.winws_manager = WinWSManager(str(WINWS_PATH), str(BIN_DIR))
        self.strategies_by_test = {}
        self.initial_accessibility = {}


    def _check_prerequisites(self):
        ui.print_header("Checking prerequisites")
        ui.print_ok("Running with administrator privileges.")
        
        required_files = [WINWS_PATH, CURL_PATH, STRATEGIES_PATH]
        for path in required_files:
            if not path.exists():
                raise BlockCheckError(f"Required file not found: '{path}'.")
        
        ui.print_ok("All required binaries and strategy file found.")
        if is_process_running('winws') or is_process_running('goodbyedpi'):
            ui.print_warn("A DPI bypass process is already running. This may interfere with results.")
            input("Press Enter to continue anyway...")

    def _check_curl_capabilities(self):
        ui.print_header("Checking curl capabilities")
        try:
            res = subprocess.run([CURL_PATH, '-V'], capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR)
            version_output = res.stdout.lower()
            self.curl_caps['tls1.3'] = 'ssl' in version_output
            print(f"TLS 1.3 support: {ui.Fore.GREEN}Yes{ui.Style.RESET_ALL}" if self.curl_caps['tls1.3'] else f"{ui.Fore.RED}No{ui.Style.RESET_ALL}")
            self.curl_caps['http3'] = 'http3' in version_output
            print(f"HTTP/3 (QUIC) support: {ui.Fore.GREEN}Yes{ui.Style.RESET_ALL}" if self.curl_caps['http3'] else f"{ui.Fore.RED}No{ui.Style.RESET_ALL}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise BlockCheckError(f"Failed to check curl capabilities: {e}")

    def ask_params(self):
        print("\n" + "="*50 + "\nBlockcheck Configuration\n" + "="*50)
        default_domains = "rutracker.org"
        domains_input = input(f"Enter domain(s) to test, separated by spaces (default: {default_domains}): ")
        self.domains = domains_input.split() if domains_input else [default_domains]
        repeats_input = input("How many times to repeat each test (default: 1): ")
        self.repeats = int(repeats_input) if repeats_input.isdigit() and int(repeats_input) > 0 else 1
        
        self.checks_to_run['http'] = ui.ask_yes_no("Check HTTP?", default_yes=DEFAULT_CHECKS.get('http', True))
        self.checks_to_run['https_tls12'] = ui.ask_yes_no("Check HTTPS (TLS 1.2)?", default_yes=DEFAULT_CHECKS.get('https_tls12', True))
        if self.curl_caps['tls1.3']:
            self.checks_to_run['https_tls13'] = ui.ask_yes_no("Check HTTPS (TLS 1.3)?", default_yes=DEFAULT_CHECKS.get('https_tls13', False))
        else:
            self.checks_to_run['https_tls13'] = False
            ui.print_warn("TLS 1.3 not supported by your curl version, skipping.")
            
        if self.curl_caps['http3']:
            self.checks_to_run['http3'] = ui.ask_yes_no("Check HTTP/3 (QUIC)?", default_yes=DEFAULT_CHECKS.get('http3', True))
        else:
            self.checks_to_run['http3'] = False
            ui.print_warn("HTTP/3 not supported by your curl version, skipping.")

    def _load_strategies_from_file(self):
        ui.print_header("Loading strategies from file")
        self.strategies_by_test = {key: [] for key in self.checks_to_run.keys()}
        try:
            with STRATEGIES_PATH.open('r') as f:
                for line in f:
                    line = line.strip()
                    if not line or ' : ' not in line: continue
                    
                    test_name_raw, params_raw = line.split(' : ', 1)
                    internal_test_name = test_name_raw.strip()
                    
                    if internal_test_name == 'https':
                        params_list = params_raw.split()[1:]
                        if self.checks_to_run.get('https_tls12'):
                            self.strategies_by_test.setdefault('https_tls12', []).append(params_list)
                        if self.checks_to_run.get('https_tls13'):
                            self.strategies_by_test.setdefault('https_tls13', []).append(params_list)
                    elif self.checks_to_run.get(internal_test_name):
                        params_list = params_raw.split()[1:]
                        self.strategies_by_test[internal_test_name].append(params_list)
        except FileNotFoundError:
            raise BlockCheckError(f"Strategy file not found at: {STRATEGIES_PATH}")
        
        for test_name, strategies in self.strategies_by_test.items():
            if self.checks_to_run.get(test_name) and strategies:
                ui.print_info(f"Loaded {len(strategies)} strategies for {test_name.upper()}.")

    def _perform_curl_test(self, domain, ip_version, port, tls_version=None, http3_only=False):
        try:
            addr_info = socket.getaddrinfo(domain, port, family=socket.AF_INET)
            ip = addr_info[0][4][0]
        except socket.gaierror:
            return (False, 6, f"Could not resolve host '{domain}' for IPv4", -1)
        
        protocol = "https" if port == 443 else "http"
        url = f"{protocol}://{domain}"
        timeout = CURL_TIMEOUT 

        cmd = [CURL_PATH, '-sS', '-I', '-A', USER_AGENT, '--max-time', str(timeout),
               '--connect-to', f"{domain}:{port}:{ip}:{port}"]
        if tls_version == "1.2": cmd.extend(['--tlsv1.2', '--tls-max', '1.2'])
        elif tls_version == "1.3": cmd.append('--tlsv1.3')
        if http3_only: cmd.append('--http3-only')
        cmd.append(url)
        
        try:
            start_time = time.perf_counter()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR)
            end_time = time.perf_counter()
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                http_status_line = output.splitlines()[0] if output else ""
                if " 30" in http_status_line:
                    for line in output.splitlines():
                        if line.lower().startswith("location:"):
                            if domain not in line.split(":", 1)[1].strip():
                                return (False, 254, "Suspicious redirection", -1)
                return (True, 0, "Success", end_time - start_time)
            return (False, result.returncode, output, -1)
        except subprocess.TimeoutExpired:
            return (False, 28, "Operation timed out", -1)

    def _run_single_test_session(self, test_func, args, repeats):
        if repeats == 1:
            return test_func(*args)
        
        fastest_time = float('inf')
        last_output = ""
        last_code = 0
        for _ in range(repeats):
            success, code, output, time_taken = test_func(*args)
            if not success: return False, code, output, -1
            if time_taken >= 0 and time_taken < fastest_time: fastest_time = time_taken
            last_output = output
            last_code = code
        return True, last_code, last_output, fastest_time

    def _process_strategy_template(self, template: list, domain: str) -> list:
        final_params = []
        for param in template:
            if "%~dp0bin\\" in param:
                key, value = param.split('=', 1)
                unquoted_value = value.strip('"')
                relative_path = unquoted_value.replace("%~dp0bin\\", "")
                full_path = BIN_DIR / relative_path
                final_params.append(f'{key}={str(full_path)}')
            else:
                final_params.append(param)
        final_params.append(f"--hostlist-domains={domain}")
        return final_params

    def _test_one_strategy(self, domain: str, template: list, test_func, test_args, repeats: int):
        winws_command = self._process_strategy_template(template, domain)
        if not self.winws_manager.start(winws_command):
            return {'success': False, 'curl_output': '', 'winws_crashed': True, 'winws_stderr': self.winws_manager.get_stderr()}
        
        success, _, curl_output, time_taken = self._run_single_test_session(test_func, test_args, repeats)
        
        winws_crashed = self.winws_manager.is_crashed()
        winws_stderr = self.winws_manager.get_stderr() if winws_crashed else ""
        self.winws_manager.stop()
        
        return {'success': success, 'time': time_taken, 'curl_output': curl_output, 'winws_crashed': winws_crashed, 'winws_stderr': winws_stderr}

    def _run_test_suite(self, test_name, internal_test_key, test_func, test_args, domain):
        ui.print_header(f"Testing {test_name.upper()} for {domain}")
        print("- Checking without DPI bypass...", end="", flush=True)
        is_available, _, _, _ = self._run_single_test_session(test_func, test_args, 1)
        self.initial_accessibility[internal_test_key] = is_available
        print(f" {ui.Fore.GREEN}ACCESSIBLE{ui.Style.RESET_ALL}" if is_available else f" {ui.Fore.RED}BLOCKED{ui.Style.RESET_ALL}")

        if is_available:
            ui.print_info("Site is initially accessible, skipping bypass tests for this protocol.")
            return

        strategy_templates = self.strategies_by_test.get(internal_test_key, [])
        if not strategy_templates:
            ui.print_info(f"No strategies to test for {test_name.upper()}.")
            return
        
        ui.print_info(f"\n- Starting tests with {len(strategy_templates)} loaded strategies...")
        for i, template in enumerate(strategy_templates):
            short_name = ' '.join(p for p in template if '--wf' not in p)
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(strategy_templates)}]{ui.Style.RESET_ALL} Testing: {short_name}")

            result = self._test_one_strategy(domain, template, test_func, test_args, self.repeats)

            if result['success']:
                status_message = f"{ui.Style.BRIGHT + ui.Fore.GREEN}SUCCESS (Time: {result['time']:.3f}s){ui.Style.RESET_ALL}"
                self._add_report(internal_test_key, template, result['time'])
                print(f"  Result: {status_message}")
            else:
                status_message = f"{ui.Fore.RED}FAILED{ui.Style.RESET_ALL}"
                if result['curl_output'] and result['curl_output'] != "Success":
                    curl_error_msg = f" {ui.Fore.YELLOW}{result['curl_output']}{ui.Style.RESET_ALL}"
                    status_message += curl_error_msg
                print(f"  Result: {status_message}")
            if result['winws_crashed']:
                print(f"    {ui.Fore.RED}WinWS CRASHED. Stderr: {result['winws_stderr'].strip()}{ui.Style.RESET_ALL}")
            
    def _add_report(self, internal_test_key, strategy_template, time_taken):
        if internal_test_key not in self.reports:
            self.reports[internal_test_key] = []
        self.reports[internal_test_key].append({"strategy": ' '.join(strategy_template), "time": time_taken})

    def run_all_tests(self):
        self._load_strategies_from_file()
        for domain in self.domains:
            self.reports = {}
            self.initial_accessibility = {}
            if self.checks_to_run.get('http'):
                self._run_test_suite("HTTP", "http", self._perform_curl_test, (domain, 4, 80), domain)
            if self.checks_to_run.get('https_tls12'):
                self._run_test_suite("HTTPS (TLS 1.2)", "https_tls12", self._perform_curl_test, (domain, 4, 443, "1.2"), domain)
            if self.checks_to_run.get('https_tls13'):
                self._run_test_suite("HTTPS (TLS 1.3)", "https_tls13", self._perform_curl_test, (domain, 4, 443, "1.3"), domain)
            if self.checks_to_run.get('http3'):
                self._run_test_suite("HTTP/3", "http3", self._perform_curl_test, (domain, 4, 443, None, True), domain)
            self.print_summary(domain)

    def print_summary(self, domain):
        ui.print_header(f"SUMMARY for {domain}")
        if not any(self.reports.values()):
            ui.print_warn(f"No working strategies found for {domain}.")
            return
        
        report_order = ['http', 'https_tls12', 'https_tls13', 'http3']
        protocol_titles = {'http': 'HTTP', 'https_tls12': 'HTTPS (TLS 1.2)', 'https_tls13': 'HTTPS (TLS 1.3)', 'http3': 'HTTP/3 (QUIC)'}
        for protocol_key in report_order:
            results = self.reports.get(protocol_key, [])
            if results:
                print(f"\n{ui.Style.BRIGHT + ui.Fore.GREEN}Successful {protocol_titles.get(protocol_key)} strategies (sorted by speed):{ui.Style.RESET_ALL}")
                sorted_results = sorted(results, key=lambda x: x['time'])
                for res in sorted_results:
                    strategy_parts = res['strategy'].split()
                    display_parts = [p for p in strategy_parts if not p.startswith('--wf-')]
                    display_strategy = ' '.join(display_parts)
                    print(f"  (Time: {res['time']:.3f}s) {display_strategy}")

    def cleanup(self):
        ui.print_info("\nCleaning up...")
        self.winws_manager.stop()