from pathlib import Path
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

from . import ui
import config
from .blockcheck.blockchecker import BlockChecker
from .blockcheck.strategy import Strategy
from .config_parser import parse_preset_file, PresetRule

@dataclass
class OptimizationResult:
    changed: bool
    strategy_params: List[str]
    avg_time: float = -1.0


def _initialize_checker(test_domain: str = None) -> BlockChecker:
    """Initializes and configures BlockChecker for optimization."""
    if test_domain is None:
        test_domain = config.DEFAULT_DOMAIN
        
    ui.print_info("Initializing checker...")
    checker = BlockChecker()
    checker._check_prerequisites()
    checker._check_curl_capabilities()
    checker.domains = [test_domain]
    checker.repeats = 1
    checker.checks_to_run = {'http': True, 'https_tls13': True, 'http3': True}
    checker._load_strategies()
    return checker


def _test_rule_strategy(checker: BlockChecker, rule: PresetRule, protocol: str) -> Tuple[bool, float, str]:
    """
    Tests current rule strategy using BlockChecker infrastructure.
    Returns: (success, avg_time, error_message)
    """
    temp_strategy = Strategy(protocol, rule.strategy_args)
    test_config = checker.CHECKS_CONFIG[rule.test_type]
    
    result = checker.strategy_tester.test_strategy(
        domains=checker.domains,
        strategy=temp_strategy,
        test_params=test_config['test_params'],
        repeats=1
    )
    
    error_msg = result.curl_output or result.winws_stderr
    return result.success, result.avg_time, error_msg


def _find_best_alternative(checker: BlockChecker, rule: PresetRule) -> Optional[Tuple[List[str], float]]:
    """Finds best working alternative strategy for failed rule."""
    # Get candidates with same desync method
    candidates = [
        s for s in checker.strategy_manager.get_strategies_for_test(rule.test_type)
        if f'--dpi-desync={rule.desync_key}' in s.params
    ]
    
    if not candidates:
        ui.print_warn(f"No alternatives found for --dpi-desync={rule.desync_key}")
        return None

    ui.print_header(f"Testing {len(candidates)} alternatives")
    
    test_config = checker.CHECKS_CONFIG[rule.test_type]
    best_time, best_params = float('inf'), None
    
    for j, strategy in enumerate(candidates, 1):
        short_name = ' '.join(p for p in strategy.params if not p.startswith('--wf-'))
        print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{j}/{len(candidates)}]{ui.Style.RESET_ALL} {short_name}")
        
        result = checker.strategy_tester.test_strategy(
            domains=checker.domains,
            strategy=strategy,
            test_params=test_config['test_params'],
            repeats=1
        )
        
        if result.success:
            print(f"  {ui.Fore.GREEN}SUCCESS (Time: {result.avg_time:.3f}s){ui.Style.RESET_ALL}")
            if result.avg_time < best_time:
                best_time = result.avg_time
                # Keep only meaningful params (no protocol filters)
                best_params = [p for p in strategy.params 
                              if not p.startswith(('--wf-', '--hostlist-domains'))]
        else:
            print(f"  {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")

    if best_params:
        return (best_params, best_time)
    return None


def _optimize_rule(checker: BlockChecker, rule: PresetRule, index: int, 
                   protocol_map: Dict[str, str]) -> Optional[List[str]]:
    """
    Optimizes single rule. Returns new params if optimization succeeded, None otherwise.
    """
    if not rule.test_type or not rule.desync_key:
        return None
        
    ui.print_header(f"Rule {index + 1}: Testing ({rule.test_type}, {rule.desync_key})")
    print(f"Original: {' '.join(rule.strategy_args)}")
    
    # Test current strategy
    protocol = protocol_map.get(rule.test_type, 'https')
    success, avg_time, error_msg = _test_rule_strategy(checker, rule, protocol)

    if success:
        ui.print_ok(f"  Result: SUCCESS (Time: {avg_time:.3f}s)")
        return None

    # Current strategy failed - find alternative
    print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
    if error_msg:
        print(f"    {ui.Fore.YELLOW}{error_msg.strip()}{ui.Style.RESET_ALL}")

    ui.print_info("\nSearching for alternatives...")
    alternative = _find_best_alternative(checker, rule)
    
    if alternative:
        best_params, best_time = alternative
        ui.print_ok(f"\nBest: {' '.join(best_params)} (Time: {best_time:.3f}s)")
        return best_params
    else:
        ui.print_warn("No working replacement found. Keeping original.")
        return None


def _patch_preset_file(original_path: Path, parsed, optimized: Dict[int, List[str]]):
    """Patches preset file by replacing optimized strategies."""
    if not optimized:
        return
        
    new_path = original_path.with_name(f"{original_path.stem}_optimized{original_path.suffix}")
    
    try:
        with original_path.open('r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        ui.print_err(f"Failed to read file: {e}")
        return
    
    # Replace old strategies with new ones
    for rule_idx, new_params in optimized.items():
        if rule_idx < len(parsed.rules):
            rule = parsed.rules[rule_idx]
            old_strategy = ' '.join(rule.strategy_args)
            new_strategy = ' '.join(new_params)
            if old_strategy != new_strategy:
                content = content.replace(old_strategy, new_strategy)
    
    try:
        with new_path.open('w', encoding='utf-8') as f:
            f.write(content)
        ui.print_ok(f"\nOptimized preset saved: {new_path.name}")
    except OSError as e:
        ui.print_err(f"Failed to write preset: {e}")


def optimize_preset():
    """Main entry point for preset optimization."""
    ui.print_header("Auto-optimize Preset")
    
    # Select file
    try:
        files = [f.name for f in config.BASE_DIR.iterdir() if f.suffix.lower() in ('.bat', '.cmd')]
        if not files:
            ui.print_err(f"No .bat/.cmd files in {config.BASE_DIR}")
            return
    except OSError as e:
        ui.print_err(f"Scan failed: {e}")
        return

    selected = ui.ask_choice("Select preset to optimize:", files)
    if not selected:
        ui.print_info("Cancelled.")
        return

    # Parse preset
    preset_path = config.BASE_DIR / selected
    parsed = parse_preset_file(preset_path)
    
    if not parsed or not parsed.rules:
        ui.print_err("Failed to parse preset or no testable rules found.")
        return

    ui.print_ok(f"Parsed {len(parsed.rules)} rules from {ui.Style.BRIGHT}{selected}{ui.Style.NORMAL}")

    # Run optimization
    checker = None
    try:
        checker = _initialize_checker()
        
        protocol_map = {'http': 'http', 'https_tls13': 'https', 'http3': 'http3'}
        optimized = {}
        
        for i, rule in enumerate(parsed.rules):
            new_params = _optimize_rule(checker, rule, i, protocol_map)
            if new_params:
                optimized[i] = new_params

        # Save results
        if not optimized:
            ui.print_ok("\nAll strategies working. No optimization needed.")
        else:
            _patch_preset_file(preset_path, parsed, optimized)

    except Exception as e:
        ui.print_err(f"Optimization error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if checker:
            checker.cleanup()