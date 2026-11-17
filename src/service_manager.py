import re
import subprocess
import time
from dataclasses import dataclass
from typing import Tuple

from config import BASE_DIR, SERVICE_NAME, WINWS_PATH

from . import config_parser, ui
from .utils import kill_process

LEGACY_SERVICE_NAME = "zapret"
KNOWN_SERVICE_NAMES = (SERVICE_NAME, LEGACY_SERVICE_NAME)


@dataclass
class ServiceInfo:
    """Service state information."""

    DISPLAY_PREFIX = "Zapret DPI Bypass"

    exists: bool
    status: str = "NOT INSTALLED"
    preset: str = "Unknown"


def uninstall() -> None:
    """Stops and removes all Zapret services and kills any running winws process."""
    for name in KNOWN_SERVICE_NAMES:
        stop(name)
        delete(name)
    kill_process(WINWS_PATH.stem)


def create(service_name: str, bin_path: str, display_name: str) -> Tuple[bool, str]:
    """Creates and starts a service. Returns (success, error_message)."""
    ret, _, stderr = _run_sc([
        "create", service_name,
        "binPath=", bin_path,
        "DisplayName=", display_name,
        "start=", "auto",
    ])

    if ret != 0:
        return False, f"Service creation failed: {stderr}"

    ret_start, _, start_stderr = _run_sc(["start", service_name])
    if ret_start != 0:
        return False, f"Failed to start service: {start_stderr}"

    return True, ""


def stop(service_name: str, timeout: float = 2.5) -> None:
    """Stops a service, waiting for it to stop; no-op if not installed or already stopped."""
    ret, _, _ = _run_sc(["stop", service_name])
    if ret == 0:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            q_ret, q_stdout, _ = _run_sc(["query", service_name])
            if q_ret != 0 or re.search(r"\bSTOPPED\b", q_stdout, re.IGNORECASE):
                return
            time.sleep(0.05)


def delete(service_name: str, timeout: float = 2.5) -> None:
    """Deletes an already stopped service, waiting for removal to finish; no-op if the service doesn't exist."""
    ret, _, _ = _run_sc(["delete", service_name])
    if ret in (0, 1072):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            q_ret, _, _ = _run_sc(["query", service_name])
            if q_ret != 0:
                return
            time.sleep(0.05)


def get_info(service_name: str) -> ServiceInfo:
    """Retrieves service information."""
    qc_ret, qc_stdout, _ = _run_sc(["qc", service_name])
    if qc_ret != 0:
        return ServiceInfo(exists=False)

    info = ServiceInfo(exists=True, status="UNKNOWN")

    query_ret, query_stdout, _ = _run_sc(["query", service_name])
    if query_ret == 0:
        match = re.search(
            r"\b(RUNNING|STOPPED|START_PENDING|STOP_PENDING)\b",
            query_stdout,
            re.IGNORECASE,
        )
        if match:
            info.status = match.group(1).upper()

    # sc.exe localizes field labels, so match any key before ':'
    preset_match = re.search(
        rf"^\s*[^:\r\n]+:\s*{re.escape(ServiceInfo.DISPLAY_PREFIX)} \((.+)\)\s*$",
        qc_stdout,
        re.MULTILINE,
    )
    if preset_match:
        info.preset = preset_match.group(1)

    return info


def _run_sc(args: list[str]) -> Tuple[int, str, str]:
    """Executes sc.exe command and returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["sc.exe"] + args,
            capture_output=True,
            text=True,
            encoding="oem",
            shell=False,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout, proc.stderr

    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except (OSError, FileNotFoundError) as e:
        return -1, "", str(e)


def create_service():
    """Interactive service creation with UI."""
    ui.print_header("Create/Update Service")

    presets = config_parser.list_presets()
    if not presets:
        ui.print_err(f"No configuration files (.bat, .cmd) found in {BASE_DIR}")
        return

    selected = ui.ask_choice("Select a configuration file:", [p.stem for p in presets])
    if not selected:
        ui.print_info("Service creation cancelled.")
        return

    selected_path = next(p for p in presets if p.stem == selected)
    ui.print_info(f"\nUsing configuration: {ui.Style.BRIGHT}{selected_path.stem}{ui.Style.NORMAL}")

    parsed = config_parser.parse_preset_file(selected_path)
    if not parsed:
        return

    if not parsed.executable_path.exists():
        ui.print_err(f"Executable not found: {parsed.executable_path}")
        return

    args = parsed.get_full_args_string()
    print(f"\n{ui.Fore.CYAN}Executable:{ui.Style.RESET_ALL} \"{parsed.executable_path}\"")
    print(f"{ui.Fore.CYAN}Arguments:{ui.Style.RESET_ALL} {args if args else '[No arguments]'}")

    ui.print_info("\nReplacing existing service and starting...")
    uninstall()

    bin_path = subprocess.list2cmdline([str(parsed.executable_path), *parsed.args])
    display_name = f"{ServiceInfo.DISPLAY_PREFIX} ({selected_path.stem})"
    success, error = create(SERVICE_NAME, bin_path, display_name)

    if success:
        ui.print_ok(f"\nService '{SERVICE_NAME}' started successfully.")
    else:
        ui.print_err(error)


def delete_service():
    """Interactive service deletion with UI."""
    ui.print_header("Delete Service")
    uninstall()
    ui.print_ok("Service(s) deleted successfully.")


def get_service_status():
    """Displays service status with UI."""
    ui.print_header("Service Status")

    info = get_info(SERVICE_NAME)
    if not info.exists:
        info = get_info(LEGACY_SERVICE_NAME)

    if info.exists:
        color = ui.Fore.GREEN if info.status == "RUNNING" else ui.Fore.RED
        print(f"\nService '{SERVICE_NAME}': {color}{info.status}{ui.Style.RESET_ALL}")
        print(f"Preset: {ui.Style.BRIGHT}{info.preset}{ui.Style.RESET_ALL}")
    else:
        print(f"Status: {ui.Fore.RED}NOT INSTALLED{ui.Style.RESET_ALL}")
