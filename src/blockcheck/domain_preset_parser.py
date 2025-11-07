from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class DomainPreset:
    """Represents a single domain preset."""
    name: str
    domains: List[str]


class DomainPresetParser:
    """Parses domain presets from a text file."""

    def __init__(self, preset_file_path: Path):
        self.preset_file_path = preset_file_path
        self.presets: Dict[str, List[DomainPreset]] = {'domain': [], 'ipset': []}
        self._load_presets()

    def _load_presets(self):
        """Loads and parses the preset file."""
        if not self.preset_file_path.exists():
            return

        try:
            with self.preset_file_path.open('r', encoding='utf-8') as f:
                lines = [line.strip() for line in f]
            
            self._parse_lines(lines)
            
        except Exception as e:
            print(f"Warning: Error loading domain presets: {e}")

    def _parse_lines(self, lines: List[str]):
        """Parses lines into presets."""
        current_section = None
        preset_name = None
        domains = []

        for line in lines:
            if not line or line.startswith('#'):
                if not line and preset_name:
                    self._save_preset(current_section, preset_name, domains)
                    preset_name = None
                    domains = []
                continue

            if line.startswith('[') and line.endswith(']'):
                if preset_name:
                    self._save_preset(current_section, preset_name, domains)
                    preset_name = None
                    domains = []
                
                section_name = line[1:-1].lower()
                current_section = self._normalize_section(section_name)
                continue

            if current_section:
                if not preset_name:
                    preset_name = line
                else:
                    domains.append(line)

        if preset_name and current_section:
            self._save_preset(current_section, preset_name, domains)

    def _normalize_section(self, section_name: str) -> Optional[str]:
        """Converts section name to normalized form."""
        if 'domain' in section_name:
            return 'domain'
        elif 'ipset' in section_name:
            return 'ipset'
        return None

    def _save_preset(self, section: Optional[str], name: str, domains: List[str]):
        """Saves a preset if valid."""
        if section and domains:
            self.presets[section].append(DomainPreset(name, domains.copy()))

    def get_presets_for_mode(self, mode: str) -> List[DomainPreset]:
        """Returns all presets for the given mode, plus a 'Custom' preset."""
        mode_presets = self.presets.get(mode.lower(), [])
        custom_preset = DomainPreset("Custom", [])
        return mode_presets + [custom_preset]

    def get_preset_by_name(self, mode: str, preset_name: str) -> Optional[DomainPreset]:
        """Returns a specific preset by name for the given mode."""
        presets = self.get_presets_for_mode(mode)
        for preset in presets:
            if preset.name == preset_name:
                return preset
        return None