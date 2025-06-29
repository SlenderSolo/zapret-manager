import os

# --- Paths and Constants ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- BlockChecker Settings ---
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