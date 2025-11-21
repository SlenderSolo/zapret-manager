from src import service_manager, ui

def create_service():
    """Interactive service creation with UI."""
    ui.print_header("Create/Update Service")
    
    service_manager.uninstall()
    ui.print_ok("Existing services removed.")

    # Get available presets
    presets = service_manager.list_presets()
    if not presets:
        ui.print_err(f"No configuration files (.bat, .cmd) found in {service_manager.BASE_DIR}")
        return

    # User selection
    selected = ui.ask_choice("Select a configuration file:", presets)
    if not selected:
        ui.print_info("Service creation cancelled.")
        return

    ui.print_info(f"\nUsing configuration: {ui.Style.BRIGHT}{selected}{ui.Style.NORMAL}")
    
    # Parse and show details
    success, parsed, error = service_manager.validate_preset(selected)
    if not success:
        ui.print_err(error)
        return
    
    args = parsed.get_full_args_string()
    print(f"\n{ui.Fore.CYAN}Executable:{ui.Style.RESET_ALL} \"{parsed.executable_path}\"")
    print(f"{ui.Fore.CYAN}Arguments:{ui.Style.RESET_ALL} {args if args else '[No arguments]'}")
    
    # Install
    ui.print_info("\nCreating and starting service...")
    success, error = service_manager.install(selected)
    
    if success:
        ui.print_ok(f"\nService '{service_manager.SERVICE_NAME}' started successfully.")
    else:
        ui.print_err(error)


def delete_service():
    """Interactive service deletion with UI."""
    ui.print_header("Delete Service")
    service_manager.uninstall()
    ui.print_ok("Service(s) deleted successfully.")


def get_service_status():
    """Displays service status with UI."""
    ui.print_header("Service Status")
    
    info = service_manager.status()
    
    if info.exists:
        color = ui.Fore.GREEN if info.status == "RUNNING" else ui.Fore.RED
        print(f"\nService '{service_manager.SERVICE_NAME}': {color}{info.status}{ui.Style.RESET_ALL}")
        print(f"Preset: {ui.Style.BRIGHT}{info.preset}{ui.Style.RESET_ALL}")
    else:
        print(f"Status: {ui.Fore.RED}NOT INSTALLED{ui.Style.RESET_ALL}")