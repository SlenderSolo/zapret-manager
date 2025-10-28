import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = BASE_DIR / "bin"
STRATEGIES_PATH = BIN_DIR / "strategies.txt"

@dataclass
class Strategy:
    """Represents a single DPI circumvention strategy."""
    protocol: str
    params: List[str]
    
    @property
    def name(self) -> str:
        """Generates a display name for the strategy."""
        return ' '.join(self.params)

    def build_command(self, domains: List[str], ipset_path: Optional[Path] = None) -> List[str]:
        """Builds the full winws command line arguments for this strategy."""
        command = []

        # 1. Basic protocol parameters
        if self.protocol == 'http':
            command.extend(['--wf-l3=ipv4', '--wf-tcp=80'])
        elif self.protocol == 'https':
            command.extend(['--wf-l3=ipv4', '--wf-tcp=443'])
        elif self.protocol == 'http3':
            command.extend(['--wf-l3=ipv4', '--wf-udp=443'])

        # 2. Add domain/ipset targeting
        if ipset_path:
            command.append(f'--ipset={ipset_path}')
        else:
            command.append(f"--hostlist-domains={','.join(domains)}")

        # 3. Add unique strategy parameters, processing paths
        for param in self.params:
            if "%~dp0" in param and "=" in param:
                key, value = param.split('=', 1)
                # %~dp0 in the original context refers to the base directory of the running script.
                # The path in the file is relative to that base directory.
                relative_path_str = value.strip('"').replace("%~dp0", "")
                full_path = BASE_DIR / relative_path_str
                full_path_str = str(full_path)
                # Handle paths with spaces
                command.append(f'{key}="{full_path_str}"' if ' ' in full_path_str else f'{key}={full_path_str}')
            else:
                command.append(param)
                
        return command

class StrategyManager:
    """Loads and manages strategies from the strategies.txt file."""
    
    def __init__(self, strategies_path: Path = STRATEGIES_PATH):
        self.strategies_path = strategies_path
        self.strategies_by_protocol: Dict[str, List[Strategy]] = {
            'http': [],
            'https': [],
            'http3': []
        }

    def load_strategies(self):
        """
        Reads the strategies file, creates Strategy objects, and categorizes them.
        """
        self.strategies_by_protocol = { 'http': [], 'https': [], 'http3': [] }
        try:
            with self.strategies_path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or ' : ' not in line:
                        continue
                    
                    proto, params_raw = line.split(' : ', 1)
                    proto = proto.strip()
                    params = params_raw.split()
                    
                    if proto in self.strategies_by_protocol:
                        strategy = Strategy(protocol=proto, params=params)
                        self.strategies_by_protocol[proto].append(strategy)

        except FileNotFoundError:
            print(f"Warning: Strategy file not found at {self.strategies_path}")

    def get_strategies_for_test(self, test_key: str) -> List[Strategy]:
        """
        Returns a list of strategies applicable to a given test key (e.g., 'https_tls12').
        """
        if test_key.startswith('https'):
            return self.strategies_by_protocol.get('https', [])
        elif test_key == 'http':
            return self.strategies_by_protocol.get('http', [])
        elif test_key == 'http3':
            return self.strategies_by_protocol.get('http3', [])
        return []
