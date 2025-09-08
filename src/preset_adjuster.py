import re
from pathlib import Path
from os import sep
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from . import ui
from . import config
from .block_checker import BlockChecker, BlockCheckError, StrategyTestResult
from .config_parser import parse_preset_file, ParsedPreset, PresetRule

@dataclass
class StrategyFindResult:
    changed: bool
    strategy_string: str

class PresetAdjuster:
    def __init__(self):
        self.checker = BlockChecker()
        self.test_cache: Dict[Tuple[str, str], StrategyFindResult] = {}
        self.all_strategies: Dict[str, List[List[str]]] = {}

    def _initialize_checker(self):
        """Initializes the BlockChecker instance for testing."""
        self.checker._check_prerequisites()
        self.checker._check_curl_capabilities()
        self.checker.domains = ["rutracker.org"]
        self.checker.repeats = 1
        self.checker.checks_to_run = {'http': True, 'https_tls13': True, 'http3': True}
        self.checker._load_strategies_from_file()
        self.all_strategies = self.checker.strategies_by_test

    def _test_strategy(self, domain: str, test_type: str, template: List[str]) -> StrategyTestResult:
        """Wrapper to test a single strategy template."""
        test_config = self.checker.CHECKS_CONFIG.get(test_type)
        if not test_config:
            return StrategyTestResult(success=False, curl_output=f"Invalid test type: {test_type}")
        
        return self.checker._test_one_strategy([domain], template, test_config['test_params'])

    def _find_best_alternative(self, domain: str, test_type: str, desync_key: str) -> Optional[StrategyTestResult]:
        """Finds and tests all alternative strategies for a given key and returns the best one."""
        candidate_templates = [t for t in self.all_strategies.get(test_type, []) if f'--dpi-desync={desync_key}' in t]
        if not candidate_templates:
            ui.print_warn(f"No similar strategies found for --dpi-desync={desync_key} and protocol {test_type}")
            return None

        ui.print_header(f"Testing {len(candidate_templates)} alternative strategies for key ({test_type}, {desync_key})")
        successful_results: List[Tuple[List[str], StrategyTestResult]] = []
        for i, template in enumerate(candidate_templates):
            short_name = ' '.join(p for p in template if not p.startswith('--wf-'))
            print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(candidate_templates)}]{ui.Style.RESET_ALL} Testing: {short_name}")
            
            result = self._test_strategy(domain, test_type, template)

            if result.success:
                successful_results.append((template, result))
                print(f"  Result: {ui.Style.BRIGHT + ui.Fore.GREEN}SUCCESS (Time: {result.avg_time:.3f}s){ui.Style.RESET_ALL}")
            else:
                print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
                if result.winws_stderr: print(f"    {ui.Fore.RED}WinWS CRASHED. Stderr: {result.winws_stderr.strip()}{ui.Style.RESET_ALL}")

        if not successful_results:
            return None

        best_template, best_result = min(successful_results, key=lambda item: item[1].avg_time)
        best_result.strategy_template = best_template
        return best_result

    def find_best_strategy_for_key(self, test_type: str, desync_key: str, original_strategy_params: List[str]) -> StrategyFindResult:
        """Finds the best working strategy for a specific protocol/desync key combination."""
        domain = self.checker.domains[0]
        original_strategy_string = " ".join(original_strategy_params)

        ui.print_header(f"Verifying original strategy for key ({test_type}, {desync_key})")
        print(f"Testing original preset config: {original_strategy_string}")

        boilerplate = []
        if test_type == 'http':
            boilerplate = ['--wf-l3=ipv4', '--wf-tcp=80']
        elif test_type in ('https_tls12', 'https_tls13'):
            boilerplate = ['--wf-l3=ipv4', '--wf-tcp=443']
        elif test_type == 'http3':
            boilerplate = ['--wf-l3=ipv4', '--wf-udp=443']
        
        original_result = self._test_strategy(domain, test_type, boilerplate + original_strategy_params)

        if original_result.success:
            ui.print_ok(f"  Result: SUCCESS. Existing strategy is working. (Time: {original_result.avg_time:.3f}s)")
            return StrategyFindResult(changed=False, strategy_string=original_strategy_string)
        
        print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}. Existing strategy is not working.")
        if original_result.curl_output: print(f"    Curl Output: {ui.Fore.YELLOW}{original_result.curl_output}{ui.Style.RESET_ALL}")
        if original_result.winws_stderr: print(f"    {ui.Fore.RED}WinWS CRASHED. Stderr: {original_result.winws_stderr.strip()}{ui.Style.RESET_ALL}")

        ui.print_info("\nSearching for alternative strategies from strategies.txt...")
        best_alternative = self._find_best_alternative(domain, test_type, desync_key)

        if not best_alternative:
            ui.print_warn(f"No working replacement found for key ({test_type}, {desync_key}). Keeping original.")
            return StrategyFindResult(changed=False, strategy_string=original_strategy_string)

        # Convert the winning template back to a clean string of parameters
        resolved_params = self.checker._process_strategy_template(best_alternative.strategy_template, [domain])
        best_strategy_string = ' '.join(p for p in resolved_params if not p.startswith(('--wf-', '--hostlist-domains')))

        ui.print_ok(f"Fastest alternative found: {best_strategy_string} (Time: {best_alternative.avg_time:.3f}s)")
        return StrategyFindResult(changed=True, strategy_string=best_strategy_string)

    def _generate_new_preset_file(self, original_path: Path, parsed_data: ParsedPreset, best_strategies_map: Dict[int, str]):
        """Generates a new, adjusted .bat preset file."""
        new_path = original_path.with_name(f"{original_path.stem}_adjusted{original_path.suffix}")
        
        def unresolve_paths(line_to_clean: str) -> str:
            bin_path = str(config.BASE_DIR / "bin") + sep
            lists_path = str(config.BASE_DIR / "lists") + sep
            line = re.sub(re.escape(lists_path), r'%LISTS%\\', line_to_clean, flags=re.IGNORECASE)
            line = re.sub(re.escape(bin_path), r'%BIN%\\', line, flags=re.IGNORECASE)
            return line.replace(sep, '\\')

        unresolved_global_args = unresolve_paths(" ".join(parsed_data.global_args))
        command_start = f'start "zapret: auto-adjusted" /min "%BIN%winws.exe" {unresolved_global_args}'

        rule_lines = []
        for i, rule in enumerate(parsed_data.rules):
            strategy_part = best_strategies_map.get(i, " ".join(rule.strategy_args))
            prefix_unresolved = unresolve_paths(" ".join(rule.prefix_args))
            strategy_unresolved = unresolve_paths(strategy_part)
            rule_lines.append(f"{prefix_unresolved} {strategy_unresolved}".strip())

        full_command = f"{command_start} ^\n" + " --new ^\n".join(rule_lines)

        try:
            with new_path.open('w', encoding='utf-8') as f:
                f.write('set "BIN=%~dp0bin\\"\n')
                f.write('set "LISTS=%~dp0lists\\"\n\n')
                f.write(full_command)
            ui.print_ok(f"\nNew preset file generated successfully: {new_path}")
        except OSError as e:
            ui.print_err(f"Failed to write new preset file: {e}")

    def run(self):
        """Main entry point to run the preset adjustment process."""
        ui.print_header("Auto-adjust Preset")
        try:
            config_files = [f.name for f in config.BASE_DIR.iterdir() if f.suffix.lower() in ('.bat', '.cmd')]
            if not config_files: raise FileNotFoundError(f"No .bat or .cmd files found in {config.BASE_DIR}")
        except OSError as e:
            ui.print_err(f"Could not scan for config files: {e}"); return
        
        selected_filename = ui.ask_choice("Please select a preset to adjust:", config_files)
        if not selected_filename: ui.print_info("Operation cancelled."); return
        
        preset_path = config.BASE_DIR / selected_filename
        parsed_data = parse_preset_file(preset_path)
        if not parsed_data or not parsed_data.rules:
            ui.print_err("Failed to parse the preset or no testable rules found."); return
            
        ui.print_ok(f"Parsed {len(parsed_data.rules)} testable rules from {ui.Style.BRIGHT}{selected_filename}{ui.Style.NORMAL}")

        try:
            self._initialize_checker()
            best_strategies_map: Dict[int, str] = {}
            changes_made = False

            for i, rule in enumerate(parsed_data.rules):
                original_strategy = " ".join(rule.strategy_args)
                if not rule.test_type or not rule.desync_key:
                    best_strategies_map[i] = original_strategy
                    continue
                
                cache_key = (rule.test_type, rule.desync_key)
                if cache_key in self.test_cache:
                    ui.print_info(f"\nUsing cached result for key {cache_key}")
                    result = self.test_cache[cache_key]
                else:
                    result = self.find_best_strategy_for_key(rule.test_type, rule.desync_key, rule.strategy_args)
                    self.test_cache[cache_key] = result
                
                if result.changed: changes_made = True
                
                # Preserve original --dpi-desync-repeats if it existed
                final_strategy = result.strategy_string
                original_repeats = next((p for p in rule.strategy_args if p.startswith('--dpi-desync-repeats')), None)
                if original_repeats and original_repeats not in final_strategy:
                    final_strategy += f" {original_repeats}"
                    ui.print_info(f"Re-applied original parameter: {original_repeats}")
                best_strategies_map[i] = final_strategy

            if not changes_made:
                ui.print_ok("\nAll strategies in the preset are working correctly. No new file generated.")
            else:
                self._generate_new_preset_file(preset_path, parsed_data, best_strategies_map)

        except (BlockCheckError, FileNotFoundError) as e: ui.print_err(str(e))
        except Exception as e:
            ui.print_err(f"An unexpected error occurred: {e}")
            import traceback; traceback.print_exc()
        finally:
            self.checker.cleanup()

def adjust_preset():
    """Public function to start the preset adjustment process."""
    adjuster = PresetAdjuster()
    adjuster.run()
