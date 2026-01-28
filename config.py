from pathlib import Path

# --- Paths and Constants ---
BASE_DIR = Path(__file__).resolve().parent
BIN_DIR = BASE_DIR / "bin"
CONFIG_DIR = BASE_DIR / "config"
LISTS_DIR = BASE_DIR / "lists"

WINWS_PATH = BIN_DIR / "winws.exe"
CURL_PATH = BIN_DIR / "curl.exe"
STRATEGIES_PATH = CONFIG_DIR / "strategies.txt"
DOMAIN_PRESETS_PATH = CONFIG_DIR / "domain_presets.txt"

SERVICE_NAME = "winws"

# --- BlockChecker Settings ---
REDIRECT_AS_SUCCESS = False
ONLY_BLOCKED_DOMAINS = False
CURL_TIMEOUT = 1
CURL_MAX_WORKERS = 10
USER_AGENT = "Mozilla"
DEFAULT_DOMAIN = "rutracker.org/forum/index.php"
YOUTUBE_DOMAIN = "www.youtube.com/manifest.webmanifest"
DEFAULT_IPSET_DOMAIN = "www.delta.com"
DEFAULT_CHECKS = {
    'http': False,
    'https_tls12': False,
    'https_tls13': True,
    'http3': False
}

# --- Rate Limiter Settings ---
TOKEN_BUCKET_CAPACITY = 20
TOKEN_BUCKET_REFILL_RATE = 15.0

# --- DNS Cache Settings ---
DNS_CACHE_TTL = 300  # 5 min
