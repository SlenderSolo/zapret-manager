"""
UI utilities and test reporting.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional
from pathlib import Path


class Fore:
    BLACK   = '\033[30m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'
    BLUE    = '\033[34m'
    CYAN    = '\033[36m'
    WHITE   = '\033[37m'
    RESET   = '\033[39m'


class Style:
    BRIGHT    = '\033[1m'
    NORMAL    = '\033[22m'
    RESET_ALL = '\033[0m'


# Color Printing
def print_info(msg): print(f"{Fore.CYAN}{msg}{Style.RESET_ALL}")
def print_ok(msg): print(f"{Fore.GREEN}{msg}{Style.RESET_ALL}")
def print_warn(msg): print(f"{Fore.YELLOW}{msg}{Style.RESET_ALL}")
def print_err(msg): print(f"{Fore.RED}{msg}{Style.RESET_ALL}")
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


# === Test Reporting ===

@dataclass
class ReportEntry:
    """Single test result entry."""
    strategy: str
    time: float


class TestReporter:
    """Collects and formats test reports."""
    
    def __init__(self):
        self.reports: Dict[str, List[ReportEntry]] = {}
    
    def add_result(self, test_key: str, strategy_name: str, avg_time: float):
        """Adds a successful test result."""
        if test_key not in self.reports:
            self.reports[test_key] = []
        self.reports[test_key].append(ReportEntry(strategy_name, avg_time))
    
    def has_results(self) -> bool:
        """Returns True if any results were collected."""
        return any(self.reports.values())
    
    def generate_summary(self, test_title: str, checks_config: dict, 
                        multiple_domains: bool, repeats: int) -> str:
        """Generates formatted text summary."""
        lines = [test_title, "=" * len(test_title) + "\n"]
        label = "Avg Time" if multiple_domains or repeats > 1 else "Time"
        
        for key, config in checks_config.items():
            results = sorted(self.reports.get(key, []), key=lambda r: r.time)
            if results:
                lines.append(f"# Successful {config['title']} strategies (sorted by speed):")
                lines.extend(f"  ({label}: {r.time:.3f}s) {r.strategy}" for r in results)
                lines.append("")
        
        return "\n".join(lines)
    
    def print_and_save(self, test_title: str, checks_config: dict,
                       multiple_domains: bool, repeats: int,
                       dns_stats: Optional[Dict[str, int]] = None,
                       save_path: Optional[Path] = None):
        """Prints summary to console and optionally saves to file."""
        print_header(test_title)
        
        if not self.has_results():
            target = "domains" if "domain" in test_title.lower() else "ipset"
            print_warn(f"No working strategies found for the given {target}.")
            return
        
        summary = self.generate_summary(test_title, checks_config, multiple_domains, repeats)
        
        # Colorized console output
        console = summary.replace("# ", Style.BRIGHT + Fore.GREEN).replace("strategies", f"strategies{Style.RESET_ALL}")
        print(console)
        
        # DNS stats
        if dns_stats:
            total = dns_stats['hits'] + dns_stats['misses']
            if total > 0:
                hit_rate = dns_stats['hits'] / total * 100
                print_info(f"DNS Cache Stats: {dns_stats['hits']} hits, {dns_stats['misses']} misses ({hit_rate:.1f}% hit rate)")
        
        # Save to file
        if save_path:
            try:
                save_path.write_text(summary, encoding='utf-8')
                print_ok(f"\nSummary report saved to: {save_path}")
            except OSError as e:
                print_err(f"Failed to save summary report: {e}")
    
    def clear(self):
        """Clears all stored results."""
        self.reports.clear()
