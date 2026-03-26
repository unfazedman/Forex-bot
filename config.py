import os

# --- API KEYS & SECRETS (Loaded Securely from Environment) ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# --- GOOGLE SHEETS CONFIG ---
SHEET_NAME = "Quant Performance Log"
STATE_TAB = "System State"
LOG_TAB = "Sheet1"

# --- TRADING PARAMETERS ---
PAIRS = ['EUR/USD', 'GBP/USD']
ATR_THRESHOLD = 1.5

# --- ALGORITHM SCORING WEIGHTS ---
WEIGHT_ATR = 20
WEIGHT_SENTIMENT = 15
WEIGHT_COT = 15
