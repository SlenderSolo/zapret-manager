import re
import subprocess
import ctypes
from typing import List, Tuple, Optional

from .config import BASE_DIR, SERVICE_NAME
from . import ui
from .config_parser import parse_preset_file

LEGACY_SERVICE_NAME = "zapret"

def _run_sc_command(command_args: List[str], quiet: bool = False) -> Tuple[int, str, str]:
    """Executes the 'sc.exe' command with the given arguments."""
    try:
        console_encoding = f'cp{ctypes.windll.kernel32.GetOEMCP()}'
    except Exception:
        console_encoding = 'utf-8'

    try:
        proc = subprocess.run(
            ["sc.exe"] + command_args,
            capture_output=True,
            shell=False,
            timeout=15,
            check=False
        )
        stdout = proc.stdout.decode(console_encoding, errors='replace')
        stderr = proc.stderr.decode(console_encoding, errors='replace')

        is_service_not_exist_error = "1060" in stdout or "1060" in stderr
        if proc.returncode != 0 and not quiet and not is_service_not_exist_error:
            ui.print_err(f"[ServiceManager] SC command failed (Code: {proc.returncode}).")

        return proc.returncode, stdout, stderr

    except subprocess.TimeoutExpired:
        if not quiet:
            ui.print_err("[ServiceManager] SC command timed out")
        return -1, "", "timeout"
    except (OSError, FileNotFoundError) as e:
        if not quiet:
            ui.print_err(f"[ServiceManager] SC command execution failed: {e}")
        return -1, "", str(e)

def _cleanup_existing_services():
    """Stops and deletes the main and legacy services."""
    ui.print_info("Stopping and removing any existing services...")
    for name in [SERVICE_NAME, LEGACY_SERVICE_NAME]:
        _run_sc_command(["stop", name], quiet=True)
        _run_sc_command(["delete", name], quiet=True)

def create_service():
    """Creates or updates the Windows service based on a selected preset."""
    ui.print_header("Create/Update Service")
    _cleanup_existing_services()

    try:
        config_files = [f.name for f in BASE_DIR.iterdir() if f.suffix.lower() in ('.bat', '.cmd')]
        if not config_files:
            ui.print_err(f"No configuration files (.bat, .cmd) found in {BASE_DIR}")
            return
    except OSError as e:
        ui.print_err(f"Error scanning for configuration files: {e}")
        return

    selected_filename = ui.ask_choice("Select a configuration file:", config_files)
    if not selected_filename:
        ui.print_info("Service creation cancelled.")
        return

    ui.print_info(f"\nUsing configuration: {ui.Style.BRIGHT}{selected_filename}{ui.Style.NORMAL}")
    ui.print_info("Parsing configuration file...")

    parsed_data = parse_preset_file(BASE_DIR / selected_filename)
    if not parsed_data:
        ui.print_err("Failed to parse parameters from the selected file.")
        return
    if not parsed_data.executable_path.exists():
        ui.print_err(f"Executable not found: {parsed_data.executable_path}")
        return

    args = parsed_data.get_full_args_string()
    bin_path_arg = f'"{parsed_data.executable_path}" {args}'.strip()
    display_name = f"Zapret DPI Bypass ({selected_filename})"
    description = f"Zapret DPI bypass based on '{selected_filename}' (Python managed)"

    print(f"\n{ui.Fore.CYAN}Service Executable:{ui.Style.RESET_ALL} \"{parsed_data.executable_path}\"")
    print(f"{ui.Fore.CYAN}Service Arguments:{ui.Style.RESET_ALL} {args if args else '[No arguments]'}")

    ui.print_info(f"\nCreating service '{SERVICE_NAME}'...")
    sc_args = ["create", SERVICE_NAME, "binPath=", bin_path_arg, "DisplayName=", display_name, "start=", "auto"]
    return_code, _, _ = _run_sc_command(sc_args)

    if return_code != 0:
        ui.print_err("Service creation failed.")
        return

    ui.print_ok(f"\nService '{SERVICE_NAME}' created successfully.")
    _run_sc_command(["description", SERVICE_NAME, f'\"{description}\"'], quiet=True)

    ui.print_info("Starting service...")
    ret_start, _, _ = _run_sc_command(["start", SERVICE_NAME])
    if ret_start == 0:
        ui.print_ok(f"Service '{SERVICE_NAME}' started successfully.")
    else:
        ui.print_err(f"Failed to start service '{SERVICE_NAME}'.")

def delete_service():
    """Stops and deletes the main and legacy services."""
    ui.print_header("Delete Service")
    codes = []
    for name in [SERVICE_NAME, LEGACY_SERVICE_NAME]:
        _run_sc_command(["stop", name], quiet=True)
        code, _, _ = _run_sc_command(["delete", name], quiet=True)
        codes.append(code)
    
    if any(c == 0 for c in codes):
        ui.print_ok("Service(s) deleted successfully.")
    else:
        ui.print_warn("No known services were found to delete.")

def _get_service_details(service_name: str) -> Optional[dict]:
    """Queries a single service and returns its status and preset name."""
    qc_ret, qc_stdout, _ = _run_sc_command(["qc", service_name], quiet=True)
    if qc_ret != 0:
        return None

    details = {'name': service_name, 'status': 'UNKNOWN', 'preset': 'Unknown'}

    query_ret, query_stdout, _ = _run_sc_command(["query", service_name], quiet=True)
    if query_ret == 0:
        match = re.search(r"^\s*(?:STATE|Состояние)\s*:\s*\d+\s*([A-Z_]+)", query_stdout, re.IGNORECASE | re.MULTILINE)
        if match:
            status_value = match.group(1).strip().upper()
            if "RUNNING" in status_value: details['status'] = "RUNNING"
            elif "STOPPED" in status_value: details['status'] = "STOPPED"
            else: details['status'] = status_value
    else:
        details['status'] = "ERROR"

    display_name_match = re.search(r"^\s*(?:DISPLAY_NAME|Выводимое_имя)\s+:\s+(.*)", qc_stdout, re.IGNORECASE | re.MULTILINE)
    if display_name_match:
        display_name_text = display_name_match.group(1).strip()
        preset_match = re.search(r"\(([^)]+)\)", display_name_text)
        if preset_match:
            details['preset'] = preset_match.group(1)
        elif display_name_text:
             details['preset'] = display_name_text

    return details

def get_service_status():
    """Queries and displays the status of the main and legacy services."""
    ui.print_header("Service Status")
    
    found_services = []
    for name in [SERVICE_NAME, LEGACY_SERVICE_NAME]:
        details = _get_service_details(name)
        if details:
            found_services.append(details)

    if not found_services:
        print(f"Service status: {ui.Fore.RED}NOT INSTALLED{ui.Style.RESET_ALL}")
        return

    for details in found_services:
        status_color = ui.Fore.GREEN if details['status'] == "RUNNING" else ui.Fore.RED
        print(f"\nService '{details['name']}' status: {status_color}{details['status']}{ui.Style.RESET_ALL}")
        print(f"Preset file: {ui.Style.BRIGHT}{details['preset']}{ui.Style.RESET_ALL}")
