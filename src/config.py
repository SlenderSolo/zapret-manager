from pathlib import Path

# --- Paths and Constants ---
BASE_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = BASE_DIR / "bin"
WINWS_PATH = BIN_DIR / "winws.exe"
CURL_PATH = BIN_DIR / "curl.exe"
STRATEGIES_PATH = BIN_DIR / "strategies.txt"
LISTS_DIR = BASE_DIR / "lists"
SERVICE_NAME = "winws"

# --- BlockChecker Settings ---
REDIRECT_AS_SUCCESS = True
ONLY_BLOCKED_DOMAINS = False
CURL_TIMEOUT = 1.5
CURL_MAX_WORKERS = 10
USER_AGENT = "Mozilla"
DEFAULT_CHECKS = {
    'http': True,
    'https_tls12': False,
    'https_tls13': True,
    'http3': True
}

# --- Rate Limiter Settings ---
TOKEN_BUCKET_CAPACITY = 10
TOKEN_BUCKET_REFILL_RATE = 10.0

# --- DNS Cache Settings ---
DNS_CACHE_TTL = 300 # 5 min