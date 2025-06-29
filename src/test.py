import os
import re
import subprocess
import ctypes

from .config import BASE_DIR, SERVICE_NAME
from . import ui
from .config_parser import parse_preset_file

def run_sc_command(command_args, quiet=False):
    """Executes an sc.exe command and captures output."""
    try:
        console_encoding = f'cp{ctypes.windll.kernel32.GetOEMCP()}'
        process = subprocess.Popen(["sc.exe"] + command_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, creationflags=subprocess.CREATE_NO_WINDOW if quiet else 0)
        stdout_bytes, stderr_bytes = process.communicate(timeout=15)
        stdout = stdout_bytes.decode(console_encoding, errors='replace')
        stderr = stderr_bytes.decode(console_encoding, errors='replace')
        
        if process.returncode != 0 and not quiet and "1060" not in stdout and "1060" not in stderr:
            ui.print_err(f"SC command failed (Code: {process.returncode}).")
            if stdout.strip(): print(f"  SC STDOUT: {stdout.strip()}")
            if stderr.strip(): print(f"  SC STDERR: {stderr.strip()}")
        return process.returncode, stdout, stderr
    except Exception as e:
        if not quiet: ui.print_err(f"SC command execution failed: {e}")
        return -1, "", str(e)

def create_service():
    """Creates or updates the Windows service based on a selected preset."""
    ui.print_header("Create/Update Service")
    
    run_sc_command(["stop", SERVICE_NAME], quiet=True)
    run_sc_command(["delete", SERVICE_NAME], quiet=True)
    run_sc_command(["stop", "zapret"], quiet=True)
    run_sc_command(["delete", "zapret"], quiet=True)
        
    try:
        config_files = [f for f in os.listdir(BASE_DIR) if f.lower().endswith(('.bat', '.cmd'))]
        if not config_files:
            ui.print_err(f"No configuration files (.bat, .cmd) found in {BASE_DIR}")
            return
    except Exception as e:
        ui.print_err(f"Error scanning for configuration files: {e}")
        return

    selected_filename = ui.ask_choice("Select a configuration file:", config_files)
    if not selected_filename:
        ui.print_info("Service creation cancelled.")
        return
    
    print(f"\nUsing configuration: {ui.Style.BRIGHT}{selected_filename}{ui.Style.NORMAL}")
    ui.print_info("Parsing configuration file...")
    
    parsed_data = parse_preset_file(os.path.join(BASE_DIR, selected_filename))
    
    if not parsed_data: 
        ui.print_err("Failed to parse parameters from the selected file.")
        return
    if not os.path.exists(parsed_data.executable_path):
        ui.print_err(f"Executable not found: {parsed_data.executable_path}")
        return
    
    args = parsed_data.get_full_args_string()

    print(f"Service Executable: \"{parsed_data.executable_path}\"")
    print(f"Service Arguments: {args if args else '[No arguments]'}")
    
    ui.print_info(f"\nPreparing to create service '{SERVICE_NAME}'...")
    
    bin_path_arg = f'"{parsed_data.executable_path}" {args}'.strip() 
    display_name = f"Zapret DPI Bypass ({selected_filename})"
    sc_args = ["create", SERVICE_NAME, "binPath=", bin_path_arg, "DisplayName=", f'"{display_name}"', "start=", "auto"]
    
    ui.print_info(f"Creating service...")
    return_code, _, _ = run_sc_command(sc_args)

    if return_code == 0:
        ui.print_ok(f"\nService '{SERVICE_NAME}' created successfully.")
        description = f"Zapret DPI bypass based on '{selected_filename}' (Python managed)"
        run_sc_command(["description", SERVICE_NAME, f'"{description}"'], quiet=True)
        ui.print_info("Starting service...")
        ret_start, _, _ = run_sc_command(["start", SERVICE_NAME])
        if ret_start == 0:
            ui.print_ok(f"Service '{SERVICE_NAME}' started successfully.")
        else:
            ui.print_err(f"Failed to start service '{SERVICE_NAME}'.")
    else:
        ui.print_err(f"Service creation failed.")

def delete_service():
    """Stops and deletes the service."""
    ui.print_header("Delete Service")
    run_sc_command(["stop", SERVICE_NAME], quiet=True)
    code1, _, _ = run_sc_command(["delete", SERVICE_NAME], quiet=True)
    run_sc_command(["stop", "zapret"], quiet=True)
    code2, _, _ = run_sc_command(["delete", "zapret"], quiet=True)
    if code1 == 0 or code2 == 0: ui.print_ok("Service(s) deleted successfully.")
    else: ui.print_warn("No known services were found.")

def get_service_status():
    """Queries and displays the service status."""
    ui.print_header("Service Status")
    service_found = False
    for name in [SERVICE_NAME, "zapret"]:
        return_code, stdout, stderr = run_sc_command(["query", name], quiet=True)
        if "1060" not in stdout and "1060" not in stderr:
            service_found = True
            status = "UNKNOWN"
            if return_code == 0 and stdout:
                match = re.search(r"^\s*(?:STATE|Состояние)\s*:\s*\d+\s*([A-Z_]+)", stdout, re.IGNORECASE | re.MULTILINE)
                if match:
                    status_value = match.group(1).strip().upper()
                    if "RUNNING" in status_value: status = "RUNNING"
                    elif "STOPPED" in status_value: status = "STOPPED"
                    else: status = status_value
            else: status = "ERROR"
            status_color = ui.Fore.GREEN if status == "RUNNING" else ui.Fore.RED
            print(f"Service '{name}' status: {status_color}{status}{ui.Style.RESET_ALL}")
    if not service_found: print(f"Service status: {ui.Fore.RED}NOT INSTALLED{ui.Style.RESET_ALL}")