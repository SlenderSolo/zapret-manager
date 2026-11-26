import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional, Tuple

from config import BASE_DIR, SERVICE_NAME, WINWS_PATH

from . import preset_parser, ui
from .utils import kill_process

LEGACY_SERVICE_NAME = "zapret"
KNOWN_SERVICE_NAMES = (SERVICE_NAME, LEGACY_SERVICE_NAME)
DISPLAY_PREFIX = "Zapret DPI Bypass"

_STATE_RE = re.compile(
    r":\s*[1-7]\s+(RUNNING|STOPPED|START_PENDING|STOP_PENDING|PAUSED|CONTINUE_PENDING|PAUSE_PENDING)\b",
    re.IGNORECASE,
)
# sc.exe localizes field labels, so match any key before ':'
_PRESET_RE = re.compile(
    rf"^\s*[^:\r\n]+:\s*{re.escape(DISPLAY_PREFIX)} \((.+)\)\s*$",
    re.MULTILINE,
)


class _StopStatus(Enum):
    NOT_FOUND = auto()
    ALREADY_STOPPED = auto()
    STOPPED = auto()
    FAILED = auto()


@dataclass
class ServiceInfo:
    """Service state information."""

    exists: bool
    name: str = SERVICE_NAME
    status: str = "NOT INSTALLED"
    preset: str = "Unknown"


def _run_sc(args: list[str]) -> Tuple[int, str, str]:
    """Executes sc.exe command and returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["sc.exe", *args],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            shell=False,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as e:
        return -1, "", str(e)


def _parse_state(output: str) -> Optional[str]:
    """Extracts SCM state name from sc.exe output."""
    match = _STATE_RE.search(output)
    return match.group(1).upper() if match else None


def _query_state(service_name: str) -> Optional[str]:
    """Returns current SCM state or None if the service does not exist."""
    ret, stdout, _ = _run_sc(["query", service_name])
    return (_parse_state(stdout) or "UNKNOWN") if ret == 0 else None


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.5) -> bool:
    """Polls predicate with short initial backoff (10ms -> 50ms) until True or timeout."""
    deadline = time.monotonic() + timeout
    delay = 0.01
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 0.05)


def _stop_service(service_name: str, timeout: float = 2.5) -> _StopStatus:
    """Attempts to stop a service gracefully and reports the exact SCM outcome."""
    ret, stdout, _ = _run_sc(["stop", service_name])
    if ret == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
        return _StopStatus.NOT_FOUND
    if ret == 1062:  # ERROR_SERVICE_NOT_ACTIVE
        return _StopStatus.ALREADY_STOPPED
    if ret == 0 and _parse_state(stdout) == "STOPPED":
        return _StopStatus.STOPPED

    if ret == 1061:  # ERROR_SERVICE_CANNOT_ACCEPT_CONTROL
        state = _query_state(service_name)
        if state is None:
            return _StopStatus.NOT_FOUND
        if state == "STOPPED":
            return _StopStatus.STOPPED
        if state != "STOP_PENDING":
            return _StopStatus.FAILED
    elif ret != 0:
        return _StopStatus.FAILED

    stopped = _wait_until(lambda: _query_state(service_name) in (None, "STOPPED"), timeout)
    return _StopStatus.STOPPED if stopped else _StopStatus.FAILED


def create(service_name: str, bin_path: str, display_name: str) -> Tuple[bool, str]:
    """Creates and starts a service. Returns (success, error_message)."""
    create_args = [
        "create", service_name,
        "binPath=", bin_path,
        "DisplayName=", display_name,
        "start=", "auto",
    ]
    ret, stdout, stderr = _run_sc(create_args)
    if ret == 1072 and _wait_until(lambda: _query_state(service_name) is None):
        ret, stdout, stderr = _run_sc(create_args)

    if ret != 0:
        return False, f"Service creation failed: {stderr.strip() or stdout.strip() or 'unknown error'}"

    ret, stdout, stderr = _run_sc(["start", service_name])
    if ret != 0:
        return False, f"Failed to start service: {stderr.strip() or stdout.strip() or 'unknown error'}"

    return True, ""


def delete(service_name: str, timeout: float = 2.5, verify_removed: bool = False) -> None:
    """Deletes a service, waiting for removal if marked for deletion; no-op if absent."""
    ret, _, _ = _run_sc(["delete", service_name])
    if ret == 1072 or (verify_removed and ret == 0):
        _wait_until(lambda: _query_state(service_name) is None, timeout)


def get_info(service_name: str) -> ServiceInfo:
    """Retrieves service information."""
    ret, qc_stdout, _ = _run_sc(["qc", service_name])
    if ret != 0:
        return ServiceInfo(exists=False, name=service_name)

    preset_match = _PRESET_RE.search(qc_stdout)
    return ServiceInfo(
        exists=True,
        name=service_name,
        status=_query_state(service_name) or "UNKNOWN",
        preset=preset_match.group(1) if preset_match else "Unknown",
    )


def stop() -> bool:
    """Stops any running Zapret service or standalone winws process."""
    for name in KNOWN_SERVICE_NAMES:
        status = _stop_service(name)
        if status is _StopStatus.STOPPED:
            return True
        if status is _StopStatus.FAILED:
            break
    return kill_process(WINWS_PATH.stem)


def uninstall() -> None:
    """Stops and removes all Zapret services and cleans up any running winws process."""
    with ThreadPoolExecutor(max_workers=len(KNOWN_SERVICE_NAMES) + 1) as pool:
        results = dict(zip(KNOWN_SERVICE_NAMES, pool.map(_stop_service, KNOWN_SERVICE_NAMES)))
        statuses = results.values()

        existing = [name for name, st in results.items() if st is not _StopStatus.NOT_FOUND]
        failed = _StopStatus.FAILED in statuses

        if failed:
            kill_process(WINWS_PATH.stem)

        futures = [pool.submit(delete, name, verify_removed=failed) for name in existing]
        if not failed and _StopStatus.STOPPED not in statuses:
            futures.append(pool.submit(kill_process, WINWS_PATH.stem))

        for future in futures:
            future.result()


def create_service():
    """Interactive service creation with UI."""
    ui.print_header("Create/Update Service")

    presets = preset_parser.list_presets()
    if not presets:
        ui.print_err(f"No configuration files (.bat, .cmd) found in {BASE_DIR}")
        return

    selected = ui.ask_choice("Select a configuration file:", [p.stem for p in presets])
    if not selected:
        ui.print_info("Service creation cancelled.")
        return

    selected_path = next(p for p in presets if p.stem == selected)
    ui.print_info(f"\nUsing configuration: {ui.Style.BRIGHT}{selected_path.stem}{ui.Style.NORMAL}")

    parsed = preset_parser.parse_preset_file(selected_path)
    if not parsed:
        return

    if not parsed.executable_path.exists():
        ui.print_err(f"Executable not found: {parsed.executable_path}")
        return

    args = parsed.get_full_args_string()
    print(f'\n{ui.Fore.CYAN}Executable:{ui.Style.RESET_ALL} "{parsed.executable_path}"')
    print(f"{ui.Fore.CYAN}Arguments:{ui.Style.RESET_ALL} {args if args else '[No arguments]'}")

    ui.print_info("\nReplacing existing service and starting...")
    uninstall()

    bin_path = subprocess.list2cmdline([str(parsed.executable_path), *parsed.args])
    display_name = f"{DISPLAY_PREFIX} ({selected_path.stem})"
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

    info = next(
        (i for name in KNOWN_SERVICE_NAMES if (i := get_info(name)).exists),
        ServiceInfo(exists=False),
    )

    if info.exists:
        color = ui.Fore.GREEN if info.status == "RUNNING" else ui.Fore.RED
        print(f"\nService '{info.name}': {color}{info.status}{ui.Style.RESET_ALL}")
        print(f"Preset: {ui.Style.BRIGHT}{info.preset}{ui.Style.RESET_ALL}")
    else:
        print(f"Status: {ui.Fore.RED}NOT INSTALLED{ui.Style.RESET_ALL}")
