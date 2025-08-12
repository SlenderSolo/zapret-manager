import re
from os import sep
from pathlib import Path
from dataclasses import dataclass, field

from . import ui
from .config import BASE_DIR

@dataclass
class PresetRule:
    """A single rule from a --new block."""
    prefix_args: list[str] = field(default_factory=list)
    strategy_args: list[str] = field(default_factory=list)
    test_type: str | None = None
    desync_key: str | None = None
    
@dataclass
class ParsedPreset:
    """Structured data from a parsed .bat preset."""
    executable_path: Path
    global_args: list[str] = field(default_factory=list)
    rules: list[PresetRule] = field(default_factory=list)

    def get_full_args_string(self) -> str:
        """Reconstructs the full arguments string."""
        arg_parts = self.global_args[:]
        is_first_rule = True
        for rule in self.rules:
            if not is_first_rule:
                arg_parts.append("--new")
            is_first_rule = False
            arg_parts.extend(rule.prefix_args)
            arg_parts.extend(rule.strategy_args)
        
        return " ".join(arg_parts)

def _parse_legacy_bat(run_bat_path: Path):
    """Extracts executable and argument tokens from a legacy .bat file."""
    script_dir = BASE_DIR
    script_dir_path_for_replace = str(script_dir) + sep
    variables = {}
    all_lines = None
    try:
        # Try common encodings for bat files
        encodings_to_try = ['utf-8', 'cp866', 'cp1251']
        for enc in encodings_to_try:
            try:
                with run_bat_path.open("r", encoding=enc) as f:
                    all_lines = f.readlines()
                break
            except UnicodeDecodeError: continue
        if all_lines is None: return None, None
        
        for line_stripped in [l.strip() for l in all_lines]:
            match = re.match(r'^\s*set\s+"([^=]+)=([^"]+)"', line_stripped, re.IGNORECASE)
            if match:
                var_name, raw_value = match.group(1).strip(), match.group(2)
                had_trailing_slash = raw_value.rstrip().endswith(("\\", "/"))
                resolved_value = raw_value.replace("%~dp0", script_dir_path_for_replace)
                resolved_value = str(Path(resolved_value))
                if had_trailing_slash and not resolved_value.endswith(sep): resolved_value += sep
                variables[var_name.upper()] = resolved_value
    except Exception: return None, None

    full_start_command = ""
    in_start_block = False
    for line_content in all_lines: 
        line_stripped = line_content.strip()
        if not in_start_block:
            if line_stripped.lower().startswith("start ") and "winws.exe" in line_stripped.lower():
                in_start_block = True
                full_start_command += line_stripped
        elif in_start_block:
            full_start_command += " " + line_stripped 
        if in_start_block and not line_stripped.endswith("^"): break 
        elif in_start_block: full_start_command = full_start_command[:-1].strip()
    
    if not full_start_command: return None, None

    def replacer(match):
        var_name = match.group(1).upper()
        return variables.get(var_name, match.group(0))
    
    final_command_substituted = re.sub(r'%(\w+)%', replacer, full_start_command, flags=re.IGNORECASE)
    final_command_substituted = final_command_substituted.replace("%~dp0", script_dir_path_for_replace)
    
    args_part = re.sub(r'^\s*start\s+', '', final_command_substituted, flags=re.IGNORECASE)
    match_title = re.match(r'^"([^"]+)"\s+', args_part) 
    if match_title: args_part = args_part[len(match_title.group(0)):].lstrip()
    if args_part.lower().startswith("/min "): args_part = args_part[5:].lstrip()
    
    tokens = []
    current_token, in_quotes_arg = "", False
    for char in args_part:
        if char == '"': in_quotes_arg = not in_quotes_arg
        if char == ' ' and not in_quotes_arg:
            if current_token: tokens.append(current_token)
            current_token = ""
        else: current_token += char
    if current_token: tokens.append(current_token)
    
    exe_token_index = -1
    for i, token in enumerate(tokens):
        clean_token = token.strip('"').replace("\\", "/")
        if clean_token.lower().endswith("winws.exe"):
            exe_token_index = i
            break
            
    if exe_token_index == -1: return None, None
    
    resolved_exe_path = Path(tokens[exe_token_index].strip('"'))
    final_args_tokens = tokens[exe_token_index + 1:]
    return resolved_exe_path, final_args_tokens


def parse_preset_file(preset_path: Path) -> ParsedPreset | None:
    """Parses a .bat preset file into a ParsedPreset object."""
    try:
        executable_path, all_args_tokens = _parse_legacy_bat(preset_path)
        if executable_path is None or all_args_tokens is None:
            return None

        # Split tokens into global and rule-specific args
        first_rule_idx = -1
        for i, arg in enumerate(all_args_tokens):
            if arg.startswith('--filter-'):
                first_rule_idx = i
                break
        
        if first_rule_idx == -1: # No rules found, only global args
            return ParsedPreset(executable_path=executable_path, global_args=all_args_tokens)

        global_args = all_args_tokens[:first_rule_idx]
        rules_args = all_args_tokens[first_rule_idx:]
        
        parsed_rules = []
        current_rule = PresetRule()

        for token in rules_args:
            # A '--new' token finalizes the current rule and starts a fresh one
            if token == '--new':
                if current_rule.prefix_args or current_rule.strategy_args:
                    parsed_rules.append(current_rule)
                current_rule = PresetRule()
                continue

            # Assign token to the correct part of the current rule
            if token.startswith('--filter-') or token.startswith('--hostlist') or token.startswith('--ipset'):
                current_rule.prefix_args.append(token)
            else:
                current_rule.strategy_args.append(token)
            
            # Extract metadata for the adjuster
            if token.startswith('--filter-tcp=80'): current_rule.test_type = 'http'
            elif token.startswith('--filter-tcp=443'): current_rule.test_type = 'https_tls13'
            elif token.startswith('--filter-udp=443'): current_rule.test_type = 'http3'
            
            if token.startswith('--dpi-desync='):
                current_rule.desync_key = token.split('=', 1)[1]
        
        # Add the last processed rule
        if current_rule.prefix_args or current_rule.strategy_args:
            parsed_rules.append(current_rule)
            
        return ParsedPreset(executable_path=executable_path, global_args=global_args, rules=parsed_rules)

    except Exception as e:
        ui.print_err(f"Error parsing {preset_path.name}: {e}")
        return None