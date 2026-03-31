import os

# --- API KEYS & SECRETS (Loaded Securely from Environment) ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# --- SUPABASE CONFIG ---
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# --- NEW SCANNER CREDENTIALS ---
GNEWS_API_KEY = os.environ.get('GNEWS_API_KEY')
TWITTER_BEARER_TOKEN = os.environ.get('TWITTER_BEARER_TOKEN')

# --- TRADING PARAMETERS ---
PAIRS = ['EUR/USD', 'GBP/USD']
ATR_THRESHOLD = 1.5

# --- ALGORITHM SCORING WEIGHTS ---
WEIGHT_ATR = 20
WEIGHT_SENTIMENT = 15
WEIGHT_COT = 15

# --- MACRO SENTIMENT KEYWORDS ---
SENTIMENT_KEYWORDS = ['Fed', 'Tariffs', 'Inflation', 'BOJ', 'Treasury', 'CPI', 'NFP', 'Trump', 'Rate', 'ECB', 'Powell']
