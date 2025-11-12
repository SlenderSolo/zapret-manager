import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from . import ui
from config import BASE_DIR

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
    for enc in ['utf-8', 'cp866', 'cp1251']:
        try:
            with path.open("r", encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    return None


def _extract_variables(lines: List[str], script_dir: Path) -> Dict[str, str]:
    """Extracts variables from 'set VAR=VALUE' lines."""
    variables = {}
    
    for line in lines:
        stripped = line.strip()
        if not stripped.lower().startswith('set '):
            continue
        
        assignment = stripped[4:].strip()
        
        # Handle quoted format
        if assignment.startswith('"') and assignment.endswith('"'):
            assignment = assignment[1:-1]
        
        if '=' not in assignment:
            continue
        
        var_name, value = assignment.split('=', 1)
        var_name = var_name.strip().upper()
        value = value.replace('%~dp0', str(script_dir) + '\\')
        
        variables[var_name] = value
    
    return variables


def _find_winws_command(lines: List[str]) -> Optional[str]:
    """Finds and reconstructs the full multi-line winws.exe command."""
    full_command = []
    in_command = False
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped or stripped.startswith('::') or stripped.startswith('REM '):
            continue
        
        if not in_command and 'winws.exe' in stripped.lower():
            in_command = True
        
        if in_command:
            # Remove continuation character
            if stripped.endswith('^'):
                full_command.append(stripped[:-1].strip())
            else:
                full_command.append(stripped)
                break
    
    return ' '.join(full_command) if full_command else None


def _substitute_variables(command: str, variables: Dict[str, str], script_dir: Path) -> str:
    """Recursively substitutes all %VAR% placeholders."""
    max_iterations = 10
    
    for _ in range(max_iterations):
        original = command
        command = command.replace('%~dp0', str(script_dir) + '\\')
        
        def replacer(match):
            var_name = match.group(1).upper()
            return variables.get(var_name, match.group(0))
        
        command = re.sub(r'%(\w+)%', replacer, command, flags=re.IGNORECASE)
        
        if command == original:
            break
    
    return command


def _tokenize_command(command_string: str) -> List[str]:
    """Tokenizes command string, quotes are removed from output."""
    tokens = []
    current_token = ""
    in_quotes = False
    
    for char in command_string:
        if char == '"':
            in_quotes = not in_quotes
            continue
        
        if char in (' ', '\t') and not in_quotes:
            if current_token:
                tokens.append(current_token)
                current_token = ""
        else:
            current_token += char
    
    if current_token:
        tokens.append(current_token)
    
    return tokens


def _extract_executable_and_args(command: str) -> Optional[Tuple[Path, List[str]]]:
    """Removes 'start' prefix and extracts executable + arguments."""
    command = re.sub(
        r'^\s*start\s+(?:"[^"]*"\s+)?(?:/\w+\s+)*',
        '',
        command,
        flags=re.IGNORECASE
    ).strip()
    
    tokens = _tokenize_command(command)
    
    for i, token in enumerate(tokens):
        if 'winws.exe' in token.lower():
            return Path(token), tokens[i + 1:]
    
    return None


def _parse_arguments_structure(args: List[str]) -> Tuple[List[str], List[PresetRule]]:
    """Splits arguments into global_args and rules."""
    first_filter_idx = None
    for i, arg in enumerate(args):
        if arg.startswith(('--filter-', '--wf-')):
            first_filter_idx = i
            break
    
    if first_filter_idx is None:
        return args, []
    
    global_args = args[:first_filter_idx]
    rules_args = args[first_filter_idx:]
    
    rules = []
    current_rule = PresetRule()
    
    for token in rules_args:
        if token == '--new':
            if current_rule.prefix_args or current_rule.strategy_args:
                rules.append(current_rule)
            current_rule = PresetRule()
            continue
        
        if token.startswith(('--filter-', '--wf-', '--hostlist', '--ipset')):
            current_rule.prefix_args.append(token)
        else:
            current_rule.strategy_args.append(token)
        
        # Extract metadata
        if token.startswith('--filter-tcp=80') or token.startswith('--wf-tcp=80'):
            current_rule.test_type = 'http'
        elif token.startswith('--filter-tcp=443') or token.startswith('--wf-tcp=443'):
            current_rule.test_type = 'https_tls13'
        elif token.startswith('--filter-udp=443') or token.startswith('--wf-udp=443'):
            current_rule.test_type = 'http3'
        elif token.startswith('--filter-l7=quic'):
            current_rule.test_type = 'http3'
        
        if token.startswith('--dpi-desync='):
            current_rule.desync_key = token.split('=', 1)[1]
    
    if current_rule.prefix_args or current_rule.strategy_args:
        rules.append(current_rule)
    
    return global_args, rules


def parse_preset_file(preset_path: Path) -> Optional[ParsedPreset]:
    """Parses a .bat/.cmd preset file into a structured ParsedPreset object."""
    try:
        lines = _read_bat_file(preset_path)
        if not lines:
            ui.print_err(f"Failed to read {preset_path.name}")
            return None
        
        variables = _extract_variables(lines, BASE_DIR)
        
        command = _find_winws_command(lines)
        if not command:
            ui.print_err(f"Could not find winws.exe command in {preset_path.name}")
            return None
        
        command = _substitute_variables(command, variables, BASE_DIR)
        
        result = _extract_executable_and_args(command)
        if not result:
            ui.print_err(f"Could not extract executable from {preset_path.name}")
            return None
        
        executable_path, args = result
        global_args, rules = _parse_arguments_structure(args)
        
        return ParsedPreset(
            executable_path=executable_path,
            global_args=global_args,
            rules=rules
        )

    except Exception as e:
        ui.print_err(f"Error parsing {preset_path.name}: {e}")
        import traceback
        traceback.print_exc()
        return None
