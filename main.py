import os
import sys
import signal
import traceback

# This allows importing from sibling directories
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.block_checker import BlockChecker, BlockCheckError
from src.service_manager import create_service, delete_service, get_service_status
from src.preset_adjuster import adjust_preset
from src import ui
from src.utils import run_as_admin, enable_ansi_support

def run_block_checker():
    """Wrapper function to run the BlockChecker."""
    ui.print_header("Running Block Checker")
    checker = BlockChecker()
    
    def signal_handler(sig, frame):
        print("\nCtrl+C detected. Terminating check...")
        checker.cleanup()
    
    original_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        checker._check_prerequisites()
        checker._check_curl_capabilities()
        checker.ask_params()
        checker.run_all_tests()
    except BlockCheckError as e:
        ui.print_err(str(e))
        ui.print_info("BlockChecker stopped due to an error.")
    except Exception as e:
        ui.print_err(f"An unexpected error occurred in BlockChecker: {e}")
        traceback.print_exc()
    finally:
        checker.cleanup()
        signal.signal(signal.SIGINT, original_handler)

def main_menu():
    """Displays the main menu and handles user input."""
    if os.name == 'nt': os.system('cls')
    while True:
        print("\n===== Zapret Manager =====")
        print("1. Create/Update Service")
        print("2. Delete Service")
        print("3. Check Service Status")
        print("4. Auto-adjust Preset")
        print("5. Run Block Checker")
        print("0. Exit")
        choice = input("Select an option: ").strip()

        if choice == '1': create_service()
        elif choice == '2': delete_service()
        elif choice == '3': get_service_status()
        elif choice == '4': adjust_preset()
        elif choice == '5': run_block_checker()
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
    