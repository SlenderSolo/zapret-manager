from pathlib import Path

# --- Paths and Constants ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- BlockChecker Settings ---
REDIRECT_AS_SUCCESS = False
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

# --- Service Manager Settings ---
SERVICE_NAME = "winws"
# --- DNS Cache Settings ---
DNS_CACHE_TTL = 300 # 5 min