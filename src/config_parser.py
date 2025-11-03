import re
from os import sep
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import ui
from .config import BASE_DIR

@dataclass
class PresetRule:
    """A single rule from a --new block."""
    prefix_args: List[str] = field(default_factory=list)
    strategy_args: List[str] = field(default_factory=list)
    test_type: Optional[str] = None
    desync_key: Optional[str] = None

@dataclass
class ParsedPreset:
    """Structured data from a parsed .bat preset."""
    executable_path: Path
    global_args: List[str] = field(default_factory=list)
    rules: List[PresetRule] = field(default_factory=list)

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

def _read_bat_file(path: Path) -> Optional[List[str]]:
    """Reads a .bat file, trying common encodings."""
    encodings_to_try = ['utf-8', 'cp866', 'cp1251']
    for enc in encodings_to_try:
        try:
            with path.open("r", encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    return None

def _parse_bat_variables(lines: List[str], script_dir: Path) -> dict[str, str]:
    """Parses 'set "VAR=VALUE"' lines from a .bat file."""
    variables = {}
    script_dir_str = str(script_dir) + sep
    for line in lines:
        match = re.match(r'^\s*set\s+"([^"]+)=([^"]+)"\s*$', line.strip(), re.IGNORECASE)
        if match:
            var_name, raw_value = match.group(1).strip(), match.group(2)
            resolved_value = raw_value.replace("%~dp0", script_dir_str)
            resolved_path = Path(resolved_value)
            if raw_value.rstrip().endswith(("/", "\\")) and not str(resolved_path).endswith(sep):
                resolved_value = str(resolved_path) + sep
            else:
                resolved_value = str(resolved_path)
            variables[var_name.upper()] = resolved_value
    return variables

def _extract_start_command(lines: List[str]) -> Optional[str]:
    """Finds and reconstructs the full 'start ...' command, handling line continuations."""
    full_command = []
    in_start_block = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not in_start_block and stripped.lower().startswith("start ") and "winws.exe" in stripped.lower():
            in_start_block = True
        
        if in_start_block:
            full_command.append(stripped.removesuffix("^").strip())
            if not stripped.endswith("^"):
                break
    return " ".join(full_command) if full_command else None

def _substitute_variables(command: str, variables: dict[str, str], script_dir: Path) -> str:
    """Substitutes %VAR% and %~dp0 placeholders in the command string."""
    def replacer(match):
        if match.group(0).lower() == '%~dp0':
            return str(script_dir) + sep
        var_name = match.group(1).upper()
        return variables.get(var_name, match.group(0))

    substituted_cmd = re.sub(r'%~dp0|%(\w+)%', replacer, command, flags=re.IGNORECASE)
    return substituted_cmd

def _tokenize_command_args(args_string: str) -> List[str]:
    """
    Splits a command string into tokens, respecting quotes.
    Quotes are used for parsing but NOT included in the resulting tokens.
    
    Example:
        Input:  '--dpi-desync-fake-tls="C:\\path with spaces\\file.bin"'
        Output: ['--dpi-desync-fake-tls=C:\\path with spaces\\file.bin']
    """
    tokens = []
    current_token = ""
    in_quotes = False
    
    for char in args_string:
        if char == '"':
            in_quotes = not in_quotes
            continue
            
        if char == ' ' and not in_quotes:
            if current_token:
                tokens.append(current_token)
            current_token = ""
        else:
            current_token += char
    
    if current_token:
        tokens.append(current_token)
    
    return tokens

def _find_executable_and_args(tokens: List[str]) -> Optional[Tuple[Path, List[str]]]:
    """Finds the executable path and its arguments from a list of tokens."""
    for i, token in enumerate(tokens):
        clean_token = token.strip('"').replace("\\", "/")
        if clean_token.lower().endswith("winws.exe"):
            exe_path = Path(token.strip('"'))
            return exe_path, tokens[i + 1:]
    return None

def _parse_legacy_bat(run_bat_path: Path) -> Optional[Tuple[Path, List[str]]]:
    """Extracts executable and argument tokens from a legacy .bat file."""
    lines = _read_bat_file(run_bat_path)
    if not lines:
        return None, None

    variables = _parse_bat_variables(lines, BASE_DIR)
    
    start_command = _extract_start_command(lines)
    if not start_command:
        return None, None

    command_with_vars = _substitute_variables(start_command, variables, BASE_DIR)
    
    args_part = re.sub(r'^\s*start\s+(?:"[^"]+"\s+)?(?:/min\s+)?', '', command_with_vars, flags=re.IGNORECASE)
    
    tokens = _tokenize_command_args(args_part)
    
    result = _find_executable_and_args(tokens)
    if not result:
        return None, None
    
    return result[0], result[1]

def parse_preset_file(preset_path: Path) -> Optional[ParsedPreset]:
    """Parses a .bat preset file into a ParsedPreset object."""
    try:
        parse_result = _parse_legacy_bat(preset_path)
        if not parse_result:
            return None
        executable_path, all_args_tokens = parse_result

        # Split tokens into global and rule-specific args
        try:
            first_rule_idx = next(i for i, arg in enumerate(all_args_tokens) if arg.startswith('--filter-'))
        except StopIteration:
             # No rules found, only global args
            return ParsedPreset(executable_path=executable_path, global_args=all_args_tokens)

        global_args = all_args_tokens[:first_rule_idx]
        rules_args = all_args_tokens[first_rule_idx:]
        
        parsed_rules: List[PresetRule] = []
        current_rule = PresetRule()

        for token in rules_args:
            if token == '--new':
                if current_rule.prefix_args or current_rule.strategy_args:
                    parsed_rules.append(current_rule)
                current_rule = PresetRule()
                continue

            if token.startswith(("--filter-", "--hostlist", "--ipset")):
                current_rule.prefix_args.append(token)
            else:
                current_rule.strategy_args.append(token)
            
            if token.startswith('--filter-tcp=80'): current_rule.test_type = 'http'
            elif token.startswith('--filter-tcp=443'): current_rule.test_type = 'https_tls13'
            elif token.startswith('--filter-udp=443'): current_rule.test_type = 'http3'
            
            if token.startswith('--dpi-desync='):
                current_rule.desync_key = token.split('=', 1)[1]
        
        if current_rule.prefix_args or current_rule.strategy_args:
            parsed_rules.append(current_rule)
            
        return ParsedPreset(executable_path=executable_path, global_args=global_args, rules=parsed_rules)

    except Exception as e:
        ui.print_err(f"Error parsing {preset_path.name}: {e}")
        return None