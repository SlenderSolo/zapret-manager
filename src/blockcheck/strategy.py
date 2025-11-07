from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional

from config import BASE_DIR

@dataclass
class Strategy:
    """Represents a single DPI circumvention strategy."""
    protocol: str
    params: List[str]
    
    PROTOCOL_FILTERS = {
        'http': ['--wf-l3=ipv4', '--wf-tcp=80'],
        'https': ['--wf-l3=ipv4', '--wf-tcp=443'],
        'http3': ['--wf-l3=ipv4', '--wf-udp=443']
    }
    
    @property
    def name(self) -> str:
        return ' '.join(self.params)

    def build_command(self, domains: List[str], ipset_path: Optional[Path] = None) -> List[str]:
        """Builds full winws command for this strategy."""
        command = self.PROTOCOL_FILTERS.get(self.protocol, []).copy()
        
        # Add targeting (ipset or domains)
        if ipset_path:
            command.append(f'--ipset={ipset_path}')
        else:
            seen = set()
            base_domains = [d.split('/')[0] for d in domains]
            unique_domains = [d for d in base_domains if not (d in seen or seen.add(d))]
            command.append(f"--hostlist-domains={','.join(unique_domains)}")
        
        # Add strategy parameters with path resolution
        for param in self.params:
            if "%~dp0" in param and "=" in param:
                key, value = param.split('=', 1)
                full_path = str(BASE_DIR / value.strip('"').replace("%~dp0", ""))
                command.append(f'{key}="{full_path}"' if ' ' in full_path else f'{key}={full_path}')
            else:
                command.append(param)
                
        return command


class StrategyManager:
    """Loads and manages strategies from strategies.txt."""
    
    TEST_KEY_MAP = {
        'http': 'http',
        'https_tls12': 'https',
        'https_tls13': 'https',
        'http3': 'http3'
    }
    
    def __init__(self, strategies_path: Path):
        self.strategies_path = strategies_path
        self.strategies: Dict[str, List[Strategy]] = {'http': [], 'https': [], 'http3': []}

    def load_strategies(self):
        """Reads strategy file and categorizes them by protocol."""
        self.strategies = {'http': [], 'https': [], 'http3': []}
        
        try:
            with self.strategies_path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and ' : ' in line:
                        proto, params_raw = line.split(' : ', 1)
                        proto = proto.strip()
                        if proto in self.strategies:
                            self.strategies[proto].append(Strategy(proto, params_raw.split()))
        except FileNotFoundError:
            print(f"Warning: Strategy file not found at {self.strategies_path}")

    def get_strategies_for_test(self, test_key: str) -> List[Strategy]:
        """Returns strategies for given test type."""
        return self.strategies.get(self.TEST_KEY_MAP.get(test_key, ''), [])