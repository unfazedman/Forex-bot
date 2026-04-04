"""
config.py - Central Configuration & Validation
Fusion Score Bot V6.0
"""

import os

# =============================================================================
# SECTION 1: TELEGRAM
# =============================================================================
TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Dedicated error bot (falls back to main bot if not set)
ERROR_BOT_TOKEN = os.environ.get('ERROR_BOT_TOKEN')
ERROR_CHAT_ID   = os.environ.get('ERROR_CHAT_ID')

# =============================================================================
# SECTION 2: TRADING DATA
# =============================================================================
TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY')

# =============================================================================
# SECTION 3: AI & SENTIMENT
# =============================================================================
GEMINI_API_KEY       = os.environ.get('GEMINI_API_KEY')
HUGGINGFACE_API_KEY  = os.environ.get('HUGGINGFACE_API_KEY')

# Gemini 2.5 Flash — VERIFIED limits from AI Studio, April 2026
# Public docs say 250 RPD. Reality (from AI Studio): 20 RPD, 5 RPM.
# Architecture decision: Gemini is a daily "golden ticket" for the
# single highest-importance article only. HuggingFace is primary.
GEMINI_RPM_LIMIT      = 5    # Verified from AI Studio
GEMINI_RPD_LIMIT      = 20   # Verified from AI Studio (NOT 250 as docs claim)
GEMINI_THROTTLE_DELAY = 13   # 60s / 5 RPM + 1s safety buffer
GEMINI_CALLS_PER_CYCLE = 2   # 1 article × 2 pairs (EUR + GBP) per run

# =============================================================================
# SECTION 4: NEWS APIS
# =============================================================================
GNEWS_API_KEY = os.environ.get('GNEWS_API_KEY')
NEWS_API_KEY  = os.environ.get('NEWS_API_KEY')

# =============================================================================
# SECTION 5: DATABASE
# =============================================================================
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# =============================================================================
# SECTION 6: TRADING PARAMETERS
# =============================================================================
PAIRS         = ['EUR/USD', 'GBP/USD']
ATR_THRESHOLD = 1.5

# =============================================================================
# SECTION 7: FUSION SCORE WEIGHTS
# These three weights + base score of 50 = max possible 110, clamped to 100.
# Empirical finding from 76-trade audit: Score 100 has 40% win rate (worst).
# Score 85 has 68.8% win rate (best). High score ≠ high quality.
# =============================================================================
WEIGHT_ATR       = 20
WEIGHT_SENTIMENT = 25
WEIGHT_COT       = 15

# =============================================================================
# SECTION 8: SENTIMENT PIPELINE PARAMETERS
# =============================================================================
SIMILARITY_THRESHOLD  = 0.85   # Fuzzy dedup threshold
MAX_ITEMS_PER_CYCLE   = 100    # Max articles collected per run
IMPORTANCE_DECAY_HOURS = 6     # Reduce importance score after this many hours
IMPORTANCE_CUTOFF_HOURS = 24   # Ignore articles older than this

# =============================================================================
# SECTION 9: COT TRACKER PARAMETERS
# Full 5-state momentum classification (v2 design)
# =============================================================================
COT_LOOKBACK_WEEKS   = 52    # 52-week window for index normalization
COT_NEUTRAL_BAND     = 0.40  # Index values 0.40-0.60 = NEUTRAL zone
# 5 states: STRONGLY_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONGLY_BEARISH
COT_STRONG_THRESHOLD = 0.75  # Index >= 0.75 = STRONGLY_BULLISH, <= 0.25 = STRONGLY_BEARISH

# =============================================================================
# SECTION 10: VALIDATION
# =============================================================================

# Variables required for EVERY component
_ALWAYS_REQUIRED = {
    'TELEGRAM_TOKEN':  TELEGRAM_TOKEN,
    'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    'SUPABASE_URL':    SUPABASE_URL,
    'SUPABASE_KEY':    SUPABASE_KEY,
}

# Variables required only for specific components
_COMPONENT_REQUIRED = {
    'volatility_atr':     {'TWELVE_DATA_KEY': TWELVE_DATA_KEY},
    'sentiment_scanner':  {'GEMINI_API_KEY': GEMINI_API_KEY, 'GNEWS_API_KEY': GNEWS_API_KEY},
    'cot_tracker':        {},   # Only needs core vars
    'performance_grader': {},   # Only needs core vars
    'bot':                {},   # Only needs core vars
}


def validate_config(component: str = None) -> bool:
    """
    Validates environment variables are present.

    Args:
        component: Optional component name to also check component-specific vars.
                   If None, only checks always-required vars.

    Returns:
        True if all required vars are set.

    Raises:
        EnvironmentError: If any required variable is missing.
                          This is a hard failure — callers should not proceed.
    """
    to_check = dict(_ALWAYS_REQUIRED)

    if component and component in _COMPONENT_REQUIRED:
        to_check.update(_COMPONENT_REQUIRED[component])

    missing = [key for key, value in to_check.items() if not value]

    if missing:
        raise EnvironmentError(
            f"CRITICAL: Missing required environment variables: {', '.join(missing)}"
        )

    return True
