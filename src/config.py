from pathlib import Path

# --- Paths and Constants ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- BlockChecker Settings ---
REDIRECT_AS_SUCCESS = True
ONLY_BLOCKED_DOMAINS = False
CURL_TIMEOUT = 1.5
USER_AGENT = "Mozilla"
DEFAULT_CHECKS = {
    'http': True,
    'https_tls12': False,
    'https_tls13': True,
    'http3': True
}

# --- Service Manager Settings ---
SERVICE_NAME = "winws"