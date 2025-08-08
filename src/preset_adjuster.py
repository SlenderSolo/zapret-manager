import os
import re

from . import ui
from . import config
from .block_checker import BlockChecker, BlockCheckError
from .config_parser import parse_preset_file

def _get_test_params(checker, test_type, domain):
    """Gets the correct curl test function and arguments for a given test type."""
    if test_type == 'http':
        return checker._perform_curl_test, (domain, 4, 80)
    elif test_type == 'https_tls13':
        return checker._perform_curl_test, (domain, 4, 443, "1.3")
    elif test_type == 'http3':
        return checker._perform_curl_test, (domain, 4, 443, None, True)
    return None, None

def _reconstruct_template(params: list) -> list:
    """Reconstructs a strategy template with placeholder paths."""
    template = []
    bin_dir_abs = os.path.join(config.BASE_DIR, "bin")
    for param in params:
        if '=' in param:
            key, value = param.split('=', 1)
            value_unquoted = value.strip('"')
            if value_unquoted.startswith(bin_dir_abs):
                relative_path = os.path.relpath(value_unquoted, bin_dir_abs)
                template_value = f'"%~dp0bin\\{relative_path.replace(os.sep, "\\")}"'
                template.append(f'{key}={template_value}')
            else:
                template.append(param)
        else:
            template.append(param)
    return template

def _unresolve_paths(line_to_clean):
    """Replaces absolute paths with placeholders like %BIN%."""
    bin_path = os.path.normpath(os.path.join(config.BASE_DIR, "bin")) + os.sep
    lists_path = os.path.normpath(os.path.join(config.BASE_DIR, "lists")) + os.sep
    line_to_clean = re.sub(re.escape(lists_path), '%LISTS%', line_to_clean, flags=re.IGNORECASE)
    line_to_clean = re.sub(re.escape(bin_path), '%BIN%', line_to_clean, flags=re.IGNORECASE)
    return line_to_clean.replace(os.sep, '\\')

def _generate_new_preset(original_path, global_args, rules, best_strategies_map):
    """Generates a new, adjusted .bat preset file."""
    path_parts = os.path.splitext(original_path)
    new_path = f"{path_parts[0]}_adjusted{path_parts[1]}"
    
    unresolved_global_args = _unresolve_paths(" ".join(global_args))
    command_start = f'start "zapret: auto-adjusted" /min "%BIN%winws.exe" {unresolved_global_args}'

    rule_lines = []
    for i, rule in enumerate(rules):
        strategy_part = best_strategies_map.get(i, " ".join(rule.strategy_args))
        
        prefix_unresolved = _unresolve_paths(" ".join(rule.prefix_args))
        strategy_unresolved = _unresolve_paths(strategy_part)
        
        full_rule_line = f"{prefix_unresolved} {strategy_unresolved}".strip()
        rule_lines.append(full_rule_line)

    full_command = f"{command_start} ^\n" + " --new ^\n".join(rule_lines)

    try:
        with open(new_path, 'w', encoding='utf-8') as f:
            f.write('set "BIN=%~dp0bin\\"\n')
            f.write('set "LISTS=%~dp0lists\\"\n\n')
            f.write(full_command)
        ui.print_ok(f"\nNew preset file generated successfully: {new_path}")
    except Exception as e:
        ui.print_err(f"Failed to write new preset file: {e}")

def find_best_strategy_for_key(checker, test_type, desync_key, domain, all_strategies, original_strategy_params):
    """Finds the best working strategy for a specific protocol/desync key combination."""
    test_func, test_args = _get_test_params(checker, test_type, domain)
    if not test_func: return (False, " ".join(original_strategy_params))
    
    original_strategy_resolved_string = " ".join(original_strategy_params)

    ui.print_header(f"Verifying original strategy for key ({test_type}, {desync_key})")
    
    reconstructed_template_parts = _reconstruct_template(original_strategy_params)
    
    boilerplate = []
    if test_type == 'http': boilerplate = ['--wf-l3=ipv4', '--wf-tcp=80']
    elif test_type == 'https_tls13': boilerplate = ['--wf-l3=ipv4', '--wf-tcp=443']
    elif test_type == 'http3': boilerplate = ['--wf-l3=ipv4', '--wf-udp=443']

    full_original_template = boilerplate + reconstructed_template_parts
    
    print(f"Testing original preset config: {original_strategy_resolved_string}")

    result = checker._test_one_strategy(domain, full_original_template, test_func, test_args, 1)

    if result['success']:
        ui.print_ok(f"  Result: SUCCESS. Existing strategy is working. (Time: {result['time']:.3f}s)")
        return (False, original_strategy_resolved_string)
    else:
        print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}. Existing strategy is not working.")
        if result['curl_output'] and result['curl_output'] != "Success":
             print(f"    Curl Output: {ui.Fore.YELLOW}{result['curl_output']}{ui.Style.RESET_ALL}")
        if result['winws_crashed']:
            print(f"    {ui.Fore.RED}WinWS CRASHED. Stderr: {result['winws_stderr'].strip()}{ui.Style.RESET_ALL}")

    ui.print_info("\nSearching for alternative strategies from strategies.txt...")
    
    candidate_templates = [t for t in all_strategies.get(test_type, []) if f'--dpi-desync={desync_key}' in t]
    
    if not candidate_templates:
        ui.print_warn(f"No similar strategies found for --dpi-desync={desync_key} and protocol {test_type}")
        return (False, original_strategy_resolved_string)

    ui.print_header(f"Testing {len(candidate_templates)} alternative strategies for key ({test_type}, {desync_key})")
    successful_results = []
    for i, template in enumerate(candidate_templates):
        short_name = ' '.join(p for p in template if not p.startswith('--wf-'))
        print(f"\n{ui.Style.BRIGHT + ui.Fore.BLUE}[{i+1}/{len(candidate_templates)}]{ui.Style.RESET_ALL} Testing: {short_name}")
        
        result = checker._test_one_strategy(domain, template, test_func, test_args, 1)

        if result['success']:
            successful_results.append({'strategy': template, 'time': result['time']})
            print(f"  Result: {ui.Style.BRIGHT + ui.Fore.GREEN}SUCCESS (Time: {result['time']:.3f}s){ui.Style.RESET_ALL}")
        else:
            print(f"  Result: {ui.Fore.RED}FAILED{ui.Style.RESET_ALL}")
            if result['winws_crashed']: print(f"    {ui.Fore.RED}WinWS CRASHED. Stderr: {result['winws_stderr'].strip()}{ui.Style.RESET_ALL}")

    if not successful_results:
        ui.print_warn(f"No working replacement found for key ({test_type}, {desync_key}).")
        return (False, original_strategy_resolved_string)

    best_result = min(successful_results, key=lambda x: x['time'])
    
    resolved_params_list = checker._process_strategy_template(best_result['strategy'], domain)
    best_strategy_resolved_string = ' '.join(p for p in resolved_params_list if not p.startswith('--wf-') and not p.startswith('--hostlist-domains'))

    ui.print_ok(f"Fastest alternative found: {best_strategy_resolved_string} (Time: {best_result['time']:.3f}s)")
    
    return (True, best_strategy_resolved_string)

def adjust_preset():
    """Finds the best working strategies for a given preset and generates a new one."""
    ui.print_header("Auto-adjust Preset")
    try:
        config_files = [f for f in os.listdir(config.BASE_DIR) if f.lower().endswith(('.bat', '.cmd'))]
        if not config_files:
            ui.print_err(f"No .bat or .cmd files found in {config.BASE_DIR}"); return
    except Exception as e: ui.print_err(f"Could not scan for config files: {e}"); return
    
    selected_filename = ui.ask_choice("Please select a preset to adjust:", config_files)
    if not selected_filename:
        ui.print_info("Operation cancelled."); return
    
    preset_path = os.path.join(config.BASE_DIR, selected_filename)
    print(f"\nUsing preset: {ui.Style.BRIGHT}{selected_filename}{ui.Style.NORMAL}")
    
    parsed_data = parse_preset_file(preset_path)
    if not parsed_data:
        ui.print_err("Failed to parse the preset file."); return
    if not parsed_data.rules:
        ui.print_err("Could not find testable rules in the preset."); return
        
    ui.print_ok(f"Parsed {len(parsed_data.rules)} testable rules.")

    checker = None
    test_cache, best_strategies_map, changes_made = {}, {}, False
    try:
        checker = BlockChecker()
        checker._check_prerequisites()
        checker._check_curl_capabilities()
        checker.domains = ["rutracker.org"]
        checker.repeats = 1
        checker.checks_to_run = {'http': True, 'https_tls13': True, 'http3': True}
        checker._load_strategies_from_file()

        for i, rule in enumerate(parsed_data.rules):
            original_strategy_string = " ".join(rule.strategy_args)

            if not rule.test_type or not rule.desync_key:
                best_strategies_map[i] = original_strategy_string
                continue
            
            cache_key = (rule.test_type, rule.desync_key)
            was_change_needed_for_key = False
            
            if cache_key in test_cache:
                ui.print_info(f"\nUsing cached result for key {cache_key}")
                was_change_needed_for_key, cached_best_string = test_cache[cache_key]
                best_strategy_base = cached_best_string if was_change_needed_for_key else original_strategy_string
            else:
                was_change_needed_for_key, best_strategy_base = find_best_strategy_for_key(
                    checker, rule.test_type, rule.desync_key, checker.domains[0],
                    checker.strategies_by_test, rule.strategy_args
                )
                test_cache[cache_key] = (was_change_needed_for_key, best_strategy_base)
            
            if best_strategy_base != original_strategy_string:
                changes_made = True
            
            final_strategy = best_strategy_base
            original_repeats = next((p for p in rule.strategy_args if p.startswith('--dpi-desync-repeats')), None)
            if original_repeats and original_repeats not in final_strategy:
                final_strategy += f" {original_repeats}"
                ui.print_info(f"Re-applied original parameter: {original_repeats}")

            best_strategies_map[i] = final_strategy
            
        if not changes_made:
            ui.print_ok("\nAll strategies in the preset are working correctly. No new file generated.")
            return

        _generate_new_preset(preset_path, parsed_data.global_args, parsed_data.rules, best_strategies_map)

    except BlockCheckError as e: ui.print_err(str(e))
    except Exception as e:
        ui.print_err(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if checker: checker.cleanup()