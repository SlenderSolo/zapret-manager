import os
import traceback

from src.blockcheck.blockchecker import BlockChecker, BlockCheckError
from src.preset_optimizer import optimize_preset
from src import ui
from src.cli_service import create_service, delete_service, get_service_status 
from src.utils import run_as_admin, enable_ansi_support


def run_blockchecker(mode='domain'):
    """Wrapper function to run the BlockChecker."""
    ui.print_header("Running Block Checker")
    
    checker = BlockChecker(test_mode=mode)
    
    try:
        checker.check_prerequisites()
        checker.check_curl_capabilities()
        checker.configure_test(test_mode=mode)
        checker.run_all_tests()
    except KeyboardInterrupt:
        ui.print_info("\nBlockChecker stopped by user.")
    except BlockCheckError as e:
        ui.print_err(str(e))
        ui.print_info("BlockChecker stopped due to an error.")
    except Exception as e:
        ui.print_err(f"An unexpected error occurred in BlockChecker: {e}")
        traceback.print_exc()
    finally:
        checker.cleanup()


def main_menu():
    """Displays the main menu and handles user input."""
    if os.name == 'nt': os.system('cls')
    
    while True:
        print("\n===== Zapret Manager =====")
        print("1. Create/Update Service")
        print("2. Delete Service")
        print("3. Service Status")
        print("4. Optimize Preset")
        print("5. Domain Block Check")
        print("6. IP Block Check")
        print("0. Exit")
        choice = input("Select an option: ").strip()
        
        if choice == '1': create_service()
        elif choice == '2': delete_service()
        elif choice == '3': get_service_status()
        elif choice == '4': optimize_preset()
        elif choice == '5': run_blockchecker(mode='domain')
        elif choice == '6': run_blockchecker(mode='ipset')
        elif choice == '0': break
        else:
            ui.print_warn("Invalid option. Please try again.")
        
        print("-" * 40)
        input("Press Enter to continue...")
        if os.name == 'nt': os.system('cls')


if __name__ == "__main__":
    enable_ansi_support()
    run_as_admin() 
    main_menu()
    print("\nExiting program.")
    