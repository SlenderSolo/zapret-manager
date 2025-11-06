import re
import os
import subprocess
import time
import socket
import threading
from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple

from ..config import USER_AGENT, CURL_TIMEOUT, BIN_DIR, CURL_PATH, REDIRECT_AS_SUCCESS
from ..utils import TokenBucket

@dataclass
class CurlTestResult:
    success: bool
    return_code: int
    output: str
    time_taken: float = -1.0
    domain: str = ""

@dataclass
class CacheEntry:
    ip_address: str
    expiry_time: float


class DNSCache:
    """Thread-safe DNS cache with TTL. Critical for performance."""
    
    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0

    def resolve(self, domain: str) -> Optional[str]:
        base_domain = domain.split('/')[0]
        
        with self._lock:
            entry = self._cache.get(base_domain)
            if entry and time.monotonic() < entry.expiry_time:
                self.cache_hits += 1
                return entry.ip_address

        self.cache_misses += 1
        try:
            ip = socket.gethostbyname(base_domain)
            with self._lock:
                self._cache[base_domain] = CacheEntry(ip, time.monotonic() + self.ttl)
            return ip
        except socket.gaierror:
            return None

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {'hits': self.cache_hits, 'misses': self.cache_misses}


class HttpResponseValidator:
    """
    HTTP response validation - complex logic that can be reused.
    Critical for correctly identifying blocks.
    """
    
    @staticmethod
    def _get_root_domain(domain: str) -> str:
        """Extracts root domain from full domain (e.g., www.spotify.com -> spotify.com)"""
        parts = domain.split('.')

        if len(parts) >= 2:
            return '.'.join(parts[-2:])
        return domain
    
    @staticmethod
    def validate(domain: str, headers: str) -> Optional[str]:
        """
        Checks HTTP response for signs of blocking.
        Returns: None if OK, otherwise string with problem description
        """
        base_domain = domain.split('/')[0]
        root_domain = HttpResponseValidator._get_root_domain(base_domain)
        lines = headers.splitlines()
        
        if not lines:
            return "Empty response from server"

        # Parse status code
        match = re.search(r'HTTP/[\d\.]+\s+(\d+)', lines[0])
        if not match:
            return "Invalid HTTP status line"
        
        status = int(match.group(1))
        
        # Check for HTTP 400 (often means server receives fake packets)
        if status == 400:
            return "HTTP 400: Bad Request. Server likely receives fakes."
        
        # Check for suspicious redirects (sign of DPI substitution)
        if 300 <= status < 400:
            if REDIRECT_AS_SUCCESS:
                return None
            
            location = next(
                (l.split(':', 1)[1].strip() for l in lines if l.lower().startswith('location:')), 
                None
            )
            if location:
                relative = not location.lower().startswith(('http://', 'https://'))
                if relative:
                    return None
                
                same_root_domain = root_domain in location.lower()
                
                if not same_root_domain:
                    return f"Suspicious redirect to: {location}"
        
        return None


class CurlRunner:
    """Performs curl tests with DNS caching and rate limiting."""
    
    def __init__(self, dns_cache: DNSCache, rate_limiter: TokenBucket):
        self.dns_cache = dns_cache
        self.rate_limiter = rate_limiter
        self.validator = HttpResponseValidator()

    @staticmethod
    def _split_domain(domain: str) -> Tuple[str, str]:
        """Splits domain/path into base_domain and path."""
        parts = domain.split('/', 1)
        return parts[0], ('/' + parts[1] if len(parts) > 1 else '')

    def _build_cmd(self, domain: str, port: int, ip: str, 
                   tls_version: Optional[str], http3_only: bool) -> List[str]:
        """Builds curl command with necessary parameters."""
        base_domain, path = self._split_domain(domain)
        protocol = "https" if port == 443 else "http"
        
        cmd = [
            str(CURL_PATH), '-sS', '-D', '-', '-o', os.devnull, 
            '-A', USER_AGENT, '--max-time', str(CURL_TIMEOUT),
            '--connect-to', f"{base_domain}:{port}:{ip}:{port}",
            f"{protocol}://{base_domain}{path}"
        ]
        
        if tls_version == "1.2":
            cmd.extend(['--tlsv1.2', '--tls-max', '1.2'])
        elif tls_version == "1.3":
            cmd.append('--tlsv1.3')
        if http3_only:
            cmd.append('--http3-only')
        
        return cmd

    def _parse_result(self, domain: str, result: subprocess.CompletedProcess) -> CurlTestResult:
        """Parses curl execution result."""
        # Handle curl errors
        if result.returncode != 0:
            err = result.stderr.strip() or f"cURL error {result.returncode}"
            return CurlTestResult(False, result.returncode, err, domain=domain)
        
        # Validate HTTP response
        error = self.validator.validate(domain, result.stdout.strip())
        if error:
            return CurlTestResult(False, 254, error, domain=domain)
        
        return CurlTestResult(True, 0, "Success", domain=domain)

    def perform_test(self, domain: str, port: int, 
                    tls_version: Optional[str] = None, 
                    http3_only: bool = False) -> CurlTestResult:
        """Performs curl test with rate limiting and DNS caching."""
        self.rate_limiter.wait_for_token()
        
        # Resolve domain through cache
        ip = self.dns_cache.resolve(domain)
        if not ip:
            base = self._split_domain(domain)[0]
            return CurlTestResult(False, 6, f"Could not resolve '{base}'", domain=domain)

        # Execute request
        cmd = self._build_cmd(domain, port, ip, tls_version, http3_only)
        
        try:
            start = time.perf_counter()
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding='latin-1',
                timeout=CURL_TIMEOUT + 2, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR
            )
            
            parsed = self._parse_result(domain, result)
            parsed.time_taken = time.perf_counter() - start
            return parsed
            
        except subprocess.TimeoutExpired:
            return CurlTestResult(False, 28, "Operation timed out", domain=domain)