import re
import os
import subprocess
import time
import socket
import threading
from dataclasses import dataclass
from typing import Dict, Optional, List

from ..config import BASE_DIR, USER_AGENT, CURL_TIMEOUT, BIN_DIR, CURL_PATH
from ..utils import TokenBucket

@dataclass
class CurlTestResult:
    """Represents the result of a single cURL test."""
    success: bool
    return_code: int
    output: str
    time_taken: float = -1.0
    domain: str = ""

@dataclass
class CacheEntry:
    """Represents a single entry in the DNS cache."""
    ip_address: str
    expiry_time: float

class DNSCache:
    """A thread-safe, in-memory DNS cache with Time-To-Live (TTL) support."""
    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0

    def resolve(self, domain: str) -> Optional[str]:
        """Resolves a domain name to an IP address, using the cache if possible."""
        with self._lock:
            entry = self._cache.get(domain)
            if entry and time.monotonic() < entry.expiry_time:
                self.cache_hits += 1
                return entry.ip_address

        # Cache miss or expired
        self.cache_misses += 1
        ip_address = self._fetch_from_dns(domain)
        if ip_address:
            with self._lock:
                self._cache[domain] = CacheEntry(
                    ip_address=ip_address,
                    expiry_time=time.monotonic() + self.ttl
                )
        return ip_address

    def _fetch_from_dns(self, domain: str) -> Optional[str]:
        """Performs the actual DNS lookup."""
        try:
            return socket.gethostbyname(domain)
        except socket.gaierror:
            return None

    def get_stats(self) -> Dict[str, int]:
        """Returns cache statistics."""
        with self._lock:
            return {'hits': self.cache_hits, 'misses': self.cache_misses}

class CurlRunner:
    """A wrapper for executing and parsing cURL commands."""
    def __init__(self, dns_cache: DNSCache, rate_limiter: TokenBucket):
        self.dns_cache = dns_cache
        self.rate_limiter = rate_limiter

    def _build_command(self, domain: str, port: int, ip: str, tls_version: Optional[str], http3_only: bool) -> List[str]:
        """Builds the cURL command list."""
        protocol = "https" if port == 443 else "http"
        url = f"{protocol}://{domain}"
        
        cmd = [
            str(CURL_PATH), '-sS', '-D', '-', '-o', os.devnull, '-A', USER_AGENT,
            '--max-time', str(CURL_TIMEOUT), '--connect-to', f"{domain}:{port}:{ip}:{port}", url
        ]
        if tls_version == "1.2": cmd.extend(['--tlsv1.2', '--tls-max', '1.2'])
        elif tls_version == "1.3": cmd.append('--tlsv1.3')
        if http3_only: cmd.append('--http3-only')
        return cmd

    def _parse_output(self, domain: str, result: subprocess.CompletedProcess) -> CurlTestResult:
        """Parses the output of a cURL command."""
        headers_output = result.stdout.strip()
        stderr_output = result.stderr.strip()
        
        if result.returncode != 0:
            return CurlTestResult(success=False, return_code=result.returncode, output=stderr_output or f"cURL error {result.returncode}", domain=domain)
        
        header_lines = headers_output.splitlines()
        if not header_lines:
            return CurlTestResult(success=False, return_code=254, output="Empty response from server", domain=domain)

        status_line = header_lines[0]
        match = re.search(r'HTTP/[\d\.]+\s+(\d+)', status_line)
        if not match:
            return CurlTestResult(success=False, return_code=254, output="Invalid HTTP status line", domain=domain)
        
        status_code = int(match.group(1))
        if status_code == 400:
            return CurlTestResult(success=False, return_code=254, output="HTTP 400: Bad Request. Likely server receives fakes.", domain=domain)

        if 300 <= status_code < 400:
            location_header = next((line.split(':', 1)[1].strip() for line in header_lines if line.lower().startswith('location:')), None)
            is_suspicious = not (location_header and domain in location_header and location_header.lower().startswith(('http://', 'https://')))
            if is_suspicious:
                return CurlTestResult(success=False, return_code=254, output=f"Suspicious redirection to: {location_header}", domain=domain)
        
        return CurlTestResult(success=True, return_code=0, output="Success", domain=domain)

    def perform_test(self, domain: str, port: int, tls_version: Optional[str] = None, http3_only: bool = False) -> CurlTestResult:
        """Executes a cURL test against a domain."""
        self.rate_limiter.wait_for_token()
        
        ip = self.dns_cache.resolve(domain)
        if not ip:
            return CurlTestResult(success=False, return_code=6, output=f"Could not resolve host '{domain}'", domain=domain)

        cmd = self._build_command(domain, port, ip, tls_version, http3_only)

        try:
            start_time = time.perf_counter()
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='latin-1', timeout=CURL_TIMEOUT + 2, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR)
            end_time = time.perf_counter()
            
            parsed_result = self._parse_output(domain, result)
            parsed_result.time_taken = end_time - start_time
            return parsed_result

        except subprocess.TimeoutExpired:
            return CurlTestResult(success=False, return_code=28, output="Operation timed out", domain=domain)