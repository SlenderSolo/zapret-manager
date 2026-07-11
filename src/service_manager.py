import re
import subprocess
import time
from typing import Tuple, Optional
from pathlib import Path
from dataclasses import dataclass

from config import BASE_DIR, SERVICE_NAME, WINWS_PATH
from .config_parser import parse_preset_file
from .utils import kill_process

LEGACY_SERVICE_NAME = "zapret"
KNOWN_SERVICE_NAMES = (SERVICE_NAME, LEGACY_SERVICE_NAME)

# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class ServiceInfo:
    """Service state information."""
    exists: bool
    status: str = "NOT INSTALLED"
    preset: str = "Unknown"

# ============================================================================
# Core Service Operations
# ============================================================================

def _run_sc(args: list[str], quiet: bool = False) -> Tuple[int, str, str]:
    """Executes sc.exe command and returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["sc.exe"] + args,
            capture_output=True,
            text=True,
            encoding='oem',
            shell=False,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return proc.returncode, proc.stdout, proc.stderr

    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except (OSError, FileNotFoundError) as e:
        return -1, "", str(e)


def _wait_deletion(service_name: str, timeout: float = 2.5) -> bool:
    """Polls until the service is deleted or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ret, _, _ = _run_sc(["query", service_name], quiet=True)
        if ret != 0:
            return True
        time.sleep(0.02)
    return False


def delete(service_name: str) -> None:
    """Stops and deletes a service, waiting for removal to finish; no-op if the service doesn't exist."""
    ret, stdout, stderr = _run_sc(["stop", service_name], quiet=True)
    if "1060" in stdout or "1060" in stderr:
        return

    ret, stdout, stderr = _run_sc(["delete", service_name], quiet=True)
    if ret == 0 or "1072" in stdout or "1072" in stderr:
        _wait_deletion(service_name)


def create(service_name: str, bin_path: str, display_name: str, description: str) -> Tuple[bool, str]:
    """Creates and starts a service. Returns (success, error_message)."""
    ret, _, stderr = _run_sc([
        "create", service_name,
        "binPath=", bin_path,
        "DisplayName=", display_name,
        "start=", "auto"
    ])

    if ret != 0:
        return False, f"Service creation failed: {stderr}"

    ret_start, _, start_stderr = _run_sc(["start", service_name])
    if ret_start != 0:
        return False, f"Failed to start service: {start_stderr}"

    _run_sc(["description", service_name, f'"{description}"'], quiet=True)

    return True, ""


def get_info(service_name: str) -> ServiceInfo:
    """Retrieves service information."""
    qc_ret, qc_stdout, _ = _run_sc(["qc", service_name], quiet=True)

    if qc_ret != 0:
        return ServiceInfo(exists=False)

    info = ServiceInfo(exists=True, status="UNKNOWN")

    # Get status
    query_ret, query_stdout, _ = _run_sc(["query", service_name], quiet=True)
    if query_ret == 0:
        match = re.search(r'\b(RUNNING|STOPPED|START_PENDING|STOP_PENDING)\b',
                         query_stdout, re.IGNORECASE)
        if match:
            info.status = match.group(1).upper()

    # Extract preset from display name
    preset_match = re.search(r'\(([^)]+)\)', qc_stdout)
    if preset_match:
        info.preset = preset_match.group(1)

    return info


# ============================================================================
# Preset Management
# ============================================================================

def find_preset_file(preset_name: str) -> Optional[Path]:
    """Finds preset file with or without extension."""
    if preset_name.lower().endswith(('.bat', '.cmd')):
        candidate = BASE_DIR / preset_name
        if candidate.exists():
            return candidate

    for ext in ['.bat', '.cmd']:
        candidate = BASE_DIR / (preset_name + ext)
        if candidate.exists():
            return candidate

    return None


def validate_preset(preset_name: str) -> Tuple[bool, Optional[object], str]:
    """Finds, parses, and validates a preset. Returns (success, parsed_data, error_message)."""
    preset_file = find_preset_file(preset_name)
    if not preset_file:
        return False, None, f"Configuration file not found: {preset_name}"

    parsed = parse_preset_file(preset_file)
    if not parsed:
        return False, None, f"Failed to parse preset file: {preset_file.name}"

    if not parsed.executable_path.exists():
        return False, None, f"Executable not found: {parsed.executable_path}"

    return True, parsed, ""


def list_presets() -> list[str]:
    """Returns available preset names (.bat/.cmd files, excluding tool*)."""
    try:
        return sorted([
            f.stem for f in BASE_DIR.iterdir()
            if f.suffix.lower() in ('.bat', '.cmd')
            and not f.stem.lower().startswith('tool')
        ])
    except OSError:
        return []


# ============================================================================
# High-Level Operations
# ============================================================================

def install(preset_name: str) -> Tuple[bool, str]:
    """
    Installs and starts service with given preset.
    Returns (success, error_message).
    """

    for name in KNOWN_SERVICE_NAMES:
        delete(name)
    kill_process(WINWS_PATH.stem)

    success, parsed, error = validate_preset(preset_name)
    if not success:
        return False, error

    args = parsed.get_full_args_string()
    bin_path = f'"{parsed.executable_path}" {args}'.strip()
    display_name = f"Zapret DPI Bypass ({preset_name})"
    description = f"Zapret DPI bypass based on '{preset_name}' (Python managed)"

    return create(SERVICE_NAME, bin_path, display_name, description)


def uninstall() -> None:
    """Removes all Zapret services and kills any running winws process."""
    for name in KNOWN_SERVICE_NAMES:
        delete(name)
    kill_process(WINWS_PATH.stem)


def status() -> ServiceInfo:
    """Returns current service status."""
    info = get_info(SERVICE_NAME)
    if not info.exists:
        info = get_info(LEGACY_SERVICE_NAME)
    return info
    