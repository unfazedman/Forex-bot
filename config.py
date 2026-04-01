import os

# --- API KEYS & SECRETS (Loaded Securely from Environment) ---
# Telegram
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Trading Data
TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY')

# AI & Sentiment
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GCP_CREDENTIALS = os.environ.get('GCP_CREDENTIALS')

# News APIs
GNEWS_API_KEY = os.environ.get('GNEWS_API_KEY')
NEWS_API_KEY = os.environ.get('NEWS_API_KEY')

# Database
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# --- TRADING PARAMETERS ---
PAIRS = ['EUR/USD', 'GBP/USD']
ATR_THRESHOLD = 1.5

# --- FUSION SCORE WEIGHTS ---
WEIGHT_ATR = 20
WEIGHT_SENTIMENT = 25
WEIGHT_COT = 15

# --- SENTIMENT SCANNER PARAMETERS ---
COLLECTOR_INTERVAL = 300  # 5 minutes
MAX_ITEMS_PER_CYCLE = 100
SIMILARITY_THRESHOLD = 0.85
GEMINI_CALLS_PER_CYCLE = 5  # Cost control
HF_CALLS_PER_CYCLE = 10

# --- VALIDATION ---
def validate_config():
    """Validates that all required environment variables are set."""
    required_vars = {
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
        'TWELVE_DATA_KEY': TWELVE_DATA_KEY,
        'GEMINI_API_KEY': GEMINI_API_KEY,
        'GNEWS_API_KEY': GNEWS_API_KEY,
        'NEWS_API_KEY': NEWS_API_KEY,
        'SUPABASE_URL': SUPABASE_URL,
        'SUPABASE_KEY': SUPABASE_KEY,
    }
    
    missing = [key for key, value in required_vars.items() if not value]
    
    if missing:
        raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")
    
    return True
