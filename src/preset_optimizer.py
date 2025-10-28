import re
from pathlib import Path
from os import sep
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from . import ui, config
from .blockcheck.blockchecker import BlockChecker, BlockCheckError, StrategyTestResult
from .config_parser import parse_preset_file, ParsedPreset, PresetRule
from .utils import running_winws

@dataclass
class OptimizationResult:
    """Result of optimizing a single rule."""
    changed: bool
    strategy_params: List[str]
    avg_time: float = -1.0


class PresetOptimizer:
    """Optimizes preset files by finding the fastest working strategies."""
    
    def __init__(self):
        self.checker = BlockChecker()
        self.cache: Dict[Tuple[str, str], OptimizationResult] = {}
        
    def _initialize_checker(self, test_domain: str = "rutracker.org"):
        """Initialize BlockChecker with minimal configuration."""
        ui.print_info("Initializing checker...")
        self.checker._check_prerequisites()
        self.checker._check_curl_capabilities()
        self.checker.domains = [test_domain]
        self.checker.repeats = 1
        self.checker.checks_to_run = {'http': True, 'https_tls13': True, 'http3': True}
        self.checker._load_strategies()

    def _build_protocol_filters(self, test_type: str) -> List[str]:
        """Build protocol-specific WinDivert filters."""
        filters = {'http': ['--wf-l3=ipv4', '--wf-tcp=80'],
                  'https_tls12': ['--wf-l3=ipv4', '--wf-tcp=443'],
                  'https_tls13': ['--wf-l3=ipv4', '--wf-tcp=443'],
                  'http3': ['--wf-l3=ipv4', '--wf-udp=443']}
        return filters.get(test_type, [])

    def _clean_param_quotes(self, param: str) -> str:
        """Remove and re-add quotes from path parameters as needed."""
        if '=' not in param or '"' not in param:
            return param
        
        key, value = param.split('=', 1)
        clean_value = value.strip('"')
        return f'{key}="{clean_value}"' if ' ' in clean_value else f'{key}={clean_value}'

    def _test_strategy(self, test_type: str, params: List[str]) -> StrategyTestResult:
        """Test strategy parameters (handles both preset and strategies.txt)."""
        test_config = self.checker.CHECKS_CONFIG.get(test_type)
        if not test_config:
            return StrategyTestResult(success=False, curl_output=f"Invalid test type: {test_type}")
        
        # Build command for preset params (already resolved paths)
        command = self._build_protocol_filters(test_type)
        command.append(f"--hostlist-domains={','.join(self.checker.domains)}")
        command.extend(self._clean_param_quotes(p) for p in params)
        
        total_time = 0
        try:
            with running_winws(self.checker.winws_manager, command):
                test_func = partial(self.checker.curl_runner.perform_test, **test_config['test_params'])
                repeated_test = partial(self.checker._run_repeated_test, test_func=test_func, repeats=1)
                
                with ThreadPoolExecutor(max_workers=config.CURL_MAX_WORKERS) as executor:
                    for result in executor.map(repeated_test, self.checker.domains):
                        if not result.success:
                            return StrategyTestResult(success=False, curl_output=result.output)
                        total_time += result.time_taken
        except RuntimeError as e:
            return StrategyTestResult(success=False, curl_output=str(e), 
                                    winws_stderr=self.checker.winws_manager.get_stderr())
        
        return StrategyTestResult(success=True, avg_time=total_time / len(self.checker.domains))

    def _find_best_alternative(self, test_type: str, desync_key: str) -> Optional[Tuple[List[str], float]]:
        """Find the best working alternative from strategies.txt. Returns (params, time)."""
        candidates = [s for s in self.checker.strategy_manager.get_strategies_for_test(test_type)
                     if f'--dpi-desync={desync_key}' in s.params]
        
        if not candidates:
            ui.print_warn(f"No alternatives found for --dpi-desync={desync_key} ({test_type})")
            return None

        ui.print_header(f"Testing {len(candidates)} alternatives for ({test_type}, {desync_key})")
        
        best_time, best_params = float('inf'), None
        
        for i, strategy in enumerate(candidates, 1):
            short_name = ' '.join(p for p in strategy.params if not p.startswith('--wf-'))
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i}/{len(candidates)}]{ui.Style.RESET_ALL} Testing: {short_name}")
            
            result = self.checker._test_one_strategy(self.checker.domains, strategy, 
                                                     self.checker.CHECKS_CONFIG[test_type]['test_params'])
            
            if result.success:
                print(f"  Result: {ui.Style.BRIGHT + ui.Fore.GREEN}SUCCESS (Time: {result.avg_time:.3f}s){ui.Style.RESET_ALL}")
                if result.avg_time < best_time:
                    best_time, best_params = result.avg_time, strategy.params
            else:
                print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
                if result.winws_stderr:
                    print(f"    {ui.Fore.RED}WinWS Error: {result.winws_stderr.strip()}{ui.Style.RESET_ALL}")

        if best_params:
            # Filter out protocol and targeting params and return with time
            clean_params = [p for p in best_params if not p.startswith(('--wf-', '--hostlist-domains'))]
            return (clean_params, best_time)
        return None

    def _get_cache_key(self, rule: PresetRule) -> str:
        """Generate unique cache key for a rule including all its parameters."""
        # Include test_type, desync_key, and all prefix args (filter, hostlist, ipset)
        prefix_key = '|'.join(sorted(rule.prefix_args))
        return f"{rule.test_type}:{rule.desync_key}:{prefix_key}"

    def _optimize_rule(self, rule: PresetRule, index: int) -> OptimizationResult:
        """Optimize a single rule by finding the best working strategy."""
        if not rule.test_type or not rule.desync_key:
            return OptimizationResult(changed=False, strategy_params=rule.strategy_args)

        cache_key = self._get_cache_key(rule)
        if cache_key in self.cache:
            ui.print_info(f"\nUsing cached result for rule {index + 1}")
            return self.cache[cache_key]

        # Test original
        ui.print_header(f"Rule {index + 1}: Verifying ({rule.test_type}, {rule.desync_key})")
        print(f"Prefix: {' '.join(rule.prefix_args)}")
        print(f"Original: {' '.join(rule.strategy_args)}")
        
        result = self._test_strategy(rule.test_type, rule.strategy_args)

        if result.success:
            ui.print_ok(f"  Result: SUCCESS (Time: {result.avg_time:.3f}s)")
            opt_result = OptimizationResult(changed=False, strategy_params=rule.strategy_args, 
                                           avg_time=result.avg_time)
            self.cache[cache_key] = opt_result
            return opt_result

        # Original failed - find alternative
        print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
        if result.curl_output:
            print(f"    {ui.Fore.YELLOW}{result.curl_output}{ui.Style.RESET_ALL}")
        if result.winws_stderr:
            print(f"    {ui.Fore.RED}{result.winws_stderr.strip()}{ui.Style.RESET_ALL}")

        ui.print_info("\nSearching for alternatives...")
        alternative = self._find_best_alternative(rule.test_type, rule.desync_key)

        if not alternative:
            ui.print_warn(f"No working replacement found. Keeping original.")
            opt_result = OptimizationResult(changed=False, strategy_params=rule.strategy_args)
            self.cache[cache_key] = opt_result
            return opt_result

        best_params, best_time = alternative
        ui.print_ok(f"Best alternative: {' '.join(best_params)} (Time: {best_time:.3f}s)")
        
        opt_result = OptimizationResult(changed=True, strategy_params=best_params, 
                                       avg_time=best_time)
        self.cache[cache_key] = opt_result
        return opt_result

    def _finalize_params(self, original: List[str], optimized: List[str]) -> List[str]:
        """Preserve important params from original that aren't in optimized."""
        repeats = next((p for p in original if p.startswith('--dpi-desync-repeats')), None)
        if repeats and repeats not in optimized:
            ui.print_info(f"Re-applying: {repeats}")
            return optimized + [repeats]
        return optimized

    def _unresolve_path(self, path_str: str) -> str:
        """Convert absolute paths back to %BIN%/%LISTS% variables."""
        result = re.sub(re.escape(str(config.BIN_DIR) + sep), r'%BIN%\\', path_str, flags=re.IGNORECASE)
        result = re.sub(re.escape(str(config.LISTS_DIR) + sep), r'%LISTS%\\', result, flags=re.IGNORECASE)
        return result.replace(sep, '\\')

    def _generate_preset(self, original: Path, parsed: ParsedPreset, optimized: Dict[int, List[str]]):
        """Generate optimized preset file with proper formatting."""
        new_path = original.with_name(f"{original.stem}_optimized{original.suffix}")

        lines = ['set "BIN=%~dp0bin\\"', 'set "LISTS=%~dp0lists\\"', 'cd /d %BIN%', '', 
                'start "zapret: auto-optimized" /min "%BIN%winws.exe" ^']
        
        # Global args (each on separate line)
        for arg in parsed.global_args:
            lines.append(f'{self._unresolve_path(arg)} ^')
        
        # Rules
        for i, rule in enumerate(parsed.rules):
            params = optimized.get(i, rule.strategy_args)
            prefix = self._unresolve_path(' '.join(rule.prefix_args))
            strategy = self._unresolve_path(' '.join(params))
            
            suffix = ' --new ^' if i < len(parsed.rules) - 1 else ''
            lines.append(f'{prefix} {strategy}{suffix}')

        try:
            new_path.write_text('\n'.join(lines), encoding='utf-8')
            ui.print_ok(f"\nOptimized preset saved: {new_path.name}")
        except OSError as e:
            ui.print_err(f"Failed to write preset: {e}")

    def run(self):
        """Main optimization workflow."""
        ui.print_header("Auto-optimize Preset")
        
        # Select and parse preset
        try:
            files = [f.name for f in config.BASE_DIR.iterdir() if f.suffix.lower() in ('.bat', '.cmd')]
            if not files:
                raise FileNotFoundError(f"No .bat/.cmd files in {config.BASE_DIR}")
        except OSError as e:
            ui.print_err(f"Scan failed: {e}")
            return

        selected = ui.ask_choice("Select preset to optimize:", files)
        if not selected:
            ui.print_info("Cancelled.")
            return

        parsed = parse_preset_file(config.BASE_DIR / selected)
        if not parsed or not parsed.rules:
            ui.print_err("Failed to parse preset or no testable rules found.")
            return

        ui.print_ok(f"Parsed {len(parsed.rules)} rules from {ui.Style.BRIGHT}{selected}{ui.Style.NORMAL}")

        # Optimize
        try:
            self._initialize_checker()
            
            optimized, changed = {}, False
            for i, rule in enumerate(parsed.rules):
                result = self._optimize_rule(rule, i)
                changed = changed or result.changed
                optimized[i] = self._finalize_params(rule.strategy_args, result.strategy_params)

            if not changed:
                ui.print_ok("\nAll strategies working. No optimization needed.")
            else:
                self._generate_preset(config.BASE_DIR / selected, parsed, optimized)

        except Exception as e:
            ui.print_err(f"Optimization error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.checker.cleanup()


def optimize_preset():
    """Public entry point for preset optimization."""
    PresetOptimizer().run()