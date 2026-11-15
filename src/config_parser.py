import re
import subprocess
from pathlib import Path, PureWindowsPath
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from . import ui

_COMMENT_RE = re.compile(r"^(@?rem(\s|$)|::)", re.IGNORECASE)
_SET_LINE_RE = re.compile(r"^@?set\s+(.+)$", re.IGNORECASE)
_SET_FLAG_RE = re.compile(r"^/[ap](?:\s|$)", re.IGNORECASE)
_NON_LAUNCH_RE = re.compile(r"^@?(?:set|taskkill|if|echo|find|findstr)(?:\s|$)", re.IGNORECASE)


@dataclass
class PresetRule:
    """A single rule from a --new block."""

    prefix_args: List[str] = field(default_factory=list)
    strategy_args: List[str] = field(default_factory=list)
    test_type: Optional[str] = None
    desync_key: Optional[str] = None


@dataclass
class ParsedPreset:
    """Structured data from a parsed .bat/.cmd preset."""

    executable_path: Path
    args: List[str] = field(default_factory=list)
    rules: List[PresetRule] = field(default_factory=list)

    def get_full_args_string(self) -> str:
        """Returns Windows CLI-quoted arguments string."""
        return subprocess.list2cmdline(self.args)


def _extract_variables(lines: List[str], script_dir: Path) -> Dict[str, str]:
    """Extracts variables from 'set VAR=VALUE' and 'set \"VAR=VALUE\"' lines."""
    variables: Dict[str, str] = {}
    dp0 = str(script_dir).rstrip("\\") + "\\"

    for line in lines:
        m = _SET_LINE_RE.match(line.strip())
        if not m:
            continue

        assignment = m.group(1).strip()
        if _SET_FLAG_RE.match(assignment):
            continue

        if len(assignment) >= 2 and assignment.startswith('"') and assignment.endswith('"'):
            assignment = assignment[1:-1]

        if "=" not in assignment:
            continue

        var_name, value = assignment.split("=", 1)
        var_name = var_name.strip().upper()
        if var_name:
            variables[var_name] = re.sub(r"%~dp0", lambda _: dp0, value, flags=re.IGNORECASE)

    return variables


def _find_winws_command(lines: List[str]) -> Optional[str]:
    """Finds and reconstructs the full multi-line winws.exe command."""
    full_command: List[str] = []
    in_command = False

    for line in lines:
        stripped = line.strip()
        if not stripped or _COMMENT_RE.match(stripped):
            continue

        if not in_command:
            if _NON_LAUNCH_RE.match(stripped) or "winws.exe" not in stripped.lower():
                continue
            in_command = True

        if stripped.endswith("^"):
            full_command.append(stripped[:-1].strip())
        else:
            full_command.append(stripped)
            break

    return " ".join(full_command) if full_command else None


def _substitute_variables(command: str, variables: Dict[str, str], script_dir: Path) -> str:
    """Recursively substitutes %~dp0 and %VAR% placeholders."""
    dp0 = str(script_dir).rstrip("\\") + "\\"

    for _ in range(10):
        original = command
        command = re.sub(r"%~dp0", lambda _: dp0, command, flags=re.IGNORECASE)
        command = re.sub(
            r"%(\w+)%",
            lambda m: variables.get(m.group(1).upper(), m.group(0)),
            command,
            flags=re.IGNORECASE,
        )
        if command == original:
            break

    return command


def _tokenize_command(command_string: str) -> List[str]:
    """Tokenizes command string into clean argv-like tokens with quotes removed."""
    tokens: List[str] = []
    current: List[str] = []
    in_quotes = False

    for char in command_string:
        if char == '"':
            in_quotes = not in_quotes
        elif char in (" ", "\t") and not in_quotes:
            if current:
                tokens.append("".join(current))
                current.clear()
        else:
            current.append(char)

    if current:
        tokens.append("".join(current))

    return tokens


def _extract_executable_and_args(command: str) -> Optional[Tuple[Path, List[str]]]:
    """Tokenizes command and extracts winws.exe path + subsequent arguments."""
    tokens = _tokenize_command(command)
    for i, token in enumerate(tokens):
        if PureWindowsPath(token).name.lower() == "winws.exe":
            return Path(token), tokens[i + 1 :]
    return None


def _parse_rules(args: List[str]) -> List[PresetRule]:
    """Parses --new rules from flat argv tokens (for optimizer metadata only)."""
    start_idx = next((i for i, a in enumerate(args) if a.startswith("--filter-")), None)
    if start_idx is None:
        start_idx = next((i for i, a in enumerate(args) if a.startswith("--wf-")), None)
    if start_idx is None:
        return []

    groups: List[List[str]] = [[]]
    for token in args[start_idx:]:
        if token == "--new":
            if groups[-1]:
                groups.append([])
        else:
            groups[-1].append(token)

    rules: List[PresetRule] = []
    for group in groups:
        if not group:
            continue
        rule = PresetRule()
        has_filter = any(t.startswith("--filter-") for t in group)

        for token in group:
            if token.startswith(("--filter-", "--wf-", "--hostlist", "--ipset")):
                rule.prefix_args.append(token)
            else:
                rule.strategy_args.append(token)

            if token.startswith("--dpi-desync="):
                rule.desync_key = token.split("=", 1)[1]

            if token.startswith("--filter-tcp=80"):
                rule.test_type = "http"
            elif token.startswith("--filter-tcp=443"):
                rule.test_type = "https_tls13"
            elif token.startswith("--filter-udp=443") or token.startswith("--filter-l7=quic"):
                rule.test_type = "http3"
            elif not has_filter and rule.test_type is None:
                if token.startswith("--wf-tcp=80"):
                    rule.test_type = "http"
                elif token.startswith("--wf-tcp=443"):
                    rule.test_type = "https_tls13"
                elif token.startswith("--wf-udp=443"):
                    rule.test_type = "http3"

        rules.append(rule)

    return rules


def parse_preset_file(preset_path: Path) -> Optional[ParsedPreset]:
    """Parses a preset file into a structured ParsedPreset object."""
    try:
        lines = preset_path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    except OSError as e:
        ui.print_err(f"Failed to read {preset_path.name}: {e}")
        return None

    try:
        script_dir = preset_path.resolve().parent
        variables = _extract_variables(lines, script_dir)

        command = _find_winws_command(lines)
        if not command:
            ui.print_err(f"Could not find winws.exe command in {preset_path.name}")
            return None

        command = _substitute_variables(command, variables, script_dir)
        result = _extract_executable_and_args(command)
        if not result:
            ui.print_err(f"Could not extract executable from {preset_path.name}")
            return None

        executable_path, args = result
        return ParsedPreset(
            executable_path=executable_path,
            args=args,
            rules=_parse_rules(args),
        )
    except Exception as e:
        ui.print_err(f"Error parsing {preset_path.name}: {e}")
        import traceback

        traceback.print_exc()
        return None
