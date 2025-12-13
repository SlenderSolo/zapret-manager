import re
import os
import subprocess
import time
import socket
import threading
from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple

from config import USER_AGENT, CURL_TIMEOUT, BIN_DIR, CURL_PATH, REDIRECT_AS_SUCCESS
from ..utils import TokenBucket

TIME_MARKER = "::TIME::"

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
    """Thread-safe DNS cache with TTL and in-flight request deduplication."""
    
    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache: Dict[str, CacheEntry] = {}
        self._in_flight: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0

    def resolve(self, domain: str) -> Optional[str]:
        base_domain = domain.partition('/')[0].lower()
        current_time = time.monotonic()
        
        with self._lock:
            entry = self._cache.get(base_domain)
            if entry and current_time < entry.expiry_time:
                self.cache_hits += 1
                return entry.ip_address
            
            if base_domain in self._in_flight:
                event = self._in_flight[base_domain]
                self.cache_hits += 1
            else:
                event = threading.Event()
                self._in_flight[base_domain] = event
                self.cache_misses += 1
                event = None
        
        if event:
            event.wait()
            with self._lock:
                entry = self._cache.get(base_domain)
                return entry.ip_address if entry else None
        
        try:
            ip = socket.gethostbyname(base_domain)
            success = True
        except socket.gaierror:
            ip = None
            success = False
        
        with self._lock:
            if success:
                self._cache[base_domain] = CacheEntry(ip, time.monotonic() + self.ttl)
            event = self._in_flight.pop(base_domain)
        
        event.set()
        return ip

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {'hits': self.cache_hits, 'misses': self.cache_misses}


class HttpResponseValidator:
    """HTTP response validation logic."""
    
    _STATUS_RE = re.compile(r'HTTP/[\d\.]+\s+(\d+)')
    _LOCATION_RE = re.compile(r'^location:\s*(.+)', re.IGNORECASE | re.MULTILINE)
    
    @staticmethod
    def _get_root_domain(domain: str) -> str:
        """Extracts root domain (last two parts)."""
        parts = domain.split('.')
        return '.'.join(parts[-2:]) if len(parts) >= 2 else domain
    
    @staticmethod
    def _check_redirect(domain: str, headers: str) -> Optional[str]:
        """Validates redirect location if present."""
        match = HttpResponseValidator._LOCATION_RE.search(headers)
        if not match:
            return None
        
        location = match.group(1).strip().lower()
        
        if not location.startswith(('http://', 'https://')):
            return None
        
        # Check if redirect stays within root domain
        base_domain = domain.partition('/')[0]
        root_domain = HttpResponseValidator._get_root_domain(base_domain)
        
        if root_domain not in location:
            return f"Suspicious redirect to: {location}"
        
        return None
    
    @staticmethod
    def validate(domain: str, headers: str) -> Optional[str]:
        """Validates HTTP response headers."""
        if not headers:
            return "Empty response from server"
        
        match = HttpResponseValidator._STATUS_RE.search(headers)
        if not match:
            return "Invalid HTTP status line"
        
        status = int(match.group(1))
        
        if status == 400:
            return "HTTP 400: Bad Request. Server likely receives fakes."
        
        if 300 <= status < 400:
            if REDIRECT_AS_SUCCESS:
                return None
            return HttpResponseValidator._check_redirect(domain, headers)
        
        return None


class CurlRunner:
    """Performs curl tests with DNS caching, rate limiting and precise timing."""
    
    def __init__(self, dns_cache: DNSCache, rate_limiter: TokenBucket):
        self.dns_cache = dns_cache
        self.rate_limiter = rate_limiter
        self.validator = HttpResponseValidator()

    @staticmethod
    def _split_domain(domain: str) -> Tuple[str, str]:
        host, _, path = domain.partition('/')
        return host, ('/' + path if path else '')

    def _build_cmd(self, domain: str, port: int, ip: str, 
                   tls_version: Optional[str], http3_only: bool) -> List[str]:
        base_domain, path = self._split_domain(domain)
        protocol = "https" if port == 443 else "http"
        
        cmd = [
            str(CURL_PATH), '-sS', '-D', '-', '-o', os.devnull, 
            '-w', f"{TIME_MARKER}%{{time_total}}",
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

    def _parse_result(self, domain: str, result: subprocess.CompletedProcess, fallback_time: float) -> CurlTestResult:
        if result.returncode != 0:
            err = result.stderr.strip() or f"cURL error {result.returncode}"
            return CurlTestResult(False, result.returncode, err, domain=domain)
        
        full_output = result.stdout.strip()
        headers, sep, time_str = full_output.rpartition(TIME_MARKER)
        
        if sep:
            headers = headers.strip()
            try:
                curl_time = float(time_str.replace(',', '.'))
            except ValueError:
                curl_time = fallback_time
        else:
            headers = time_str
            curl_time = fallback_time
        
        error = self.validator.validate(domain, headers)
        if error:
            return CurlTestResult(False, 254, error, domain=domain)
        
        return CurlTestResult(True, 0, "Success", time_taken=curl_time, domain=domain)

    def perform_test(self, domain: str, port: int, 
                    tls_version: Optional[str] = None, 
                    http3_only: bool = False) -> CurlTestResult:
        self.rate_limiter.wait_for_token()
        
        ip = self.dns_cache.resolve(domain)
        if not ip:
            base = self._split_domain(domain)[0]
            return CurlTestResult(False, 6, f"Could not resolve '{base}'", domain=domain)

        cmd = self._build_cmd(domain, port, ip, tls_version, http3_only)
        
        try:
            start_fallback = time.perf_counter()
            
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding='latin-1',
                timeout=CURL_TIMEOUT + 2, creationflags=subprocess.CREATE_NO_WINDOW, cwd=BIN_DIR
            )
            
            fallback_time = time.perf_counter() - start_fallback
            
            return self._parse_result(domain, result, fallback_time)
            
        except subprocess.TimeoutExpired:
            return CurlTestResult(False, 28, "Operation timed out", domain=domain)
