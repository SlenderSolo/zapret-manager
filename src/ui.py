from colorama import Fore, Style

# Color Printing
def print_info(msg): print(f"{Fore.CYAN}{msg}{Style.RESET_ALL}")
def print_ok(msg): print(f"{Fore.GREEN}{msg}{Style.RESET_ALL}")
def print_warn(msg): print(f"{Fore.YELLOW}WARNING! {msg}{Style.RESET_ALL}")
def print_err(msg): print(f"{Fore.RED}ERROR! {msg}{Style.RESET_ALL}")
def print_header(msg): print(f"\n{Style.BRIGHT + Fore.WHITE}* {msg}{Style.RESET_ALL}")

# User Interaction
def ask_yes_no(question, default_yes=True):
    options = "[Y/n]" if default_yes else "[y/N]"
    default_str = "Y" if default_yes else "N"
    prompt = f"{question} (default: {default_str}) {options}: "
    
    while True:
        choice = input(prompt).strip().lower()
        if not choice: return default_yes
        if choice in ['y', 'yes']: return True
        if choice in ['n', 'no']: return False
        print_warn("Please answer 'y' or 'n'.")

def ask_choice(prompt, choices):
    """Asks user to select from a list of choices."""
    print(prompt)
    for i, item in enumerate(choices, 1):
        print(f"  {i}. {item}")
    print("  0. Cancel")
    
    while True:
        try:
            choice_str = input("Enter your choice: ").strip()
            choice = int(choice_str)
            if choice == 0: return None
            if 1 <= choice <= len(choices):
                return choices[choice - 1]
            else: print_warn("Invalid choice. Please enter a number from the list.")
        except ValueError:
            print_warn("Invalid input. Please enter a number.")