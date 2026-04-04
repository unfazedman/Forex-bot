"""
shared_functions.py - Core Shared Utilities
Fusion Score Bot V6.0

Contains:
- Supabase client (singleton pattern)
- Telegram error notifications
- Fusion Score calculation
- Sentiment aggregation (the missing link that feeds macro_sentiment)
"""

import logging
import telebot
from supabase import create_client, Client
from config import (
    WEIGHT_ATR, WEIGHT_SENTIMENT, WEIGHT_COT,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    ERROR_BOT_TOKEN, ERROR_CHAT_ID,
    SUPABASE_URL, SUPABASE_KEY
)

logger = logging.getLogger(__name__)

# =============================================================================
# SECTION 1: SUPABASE CLIENT (Singleton)
# Old bug: get_supabase_client() was called inside process_signal() on every
# trade, creating a new connection each time. Now it's a module-level singleton.
# =============================================================================

_supabase_client: Client = None

def get_supabase_client() -> Client:
    """
    Returns a shared Supabase client (singleton).
    Creates it on first call, reuses on all subsequent calls.

    Raises:
        Exception: If SUPABASE_URL or SUPABASE_KEY are missing.
    """
    global _supabase_client

    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise Exception(
                "CRITICAL: SUPABASE_URL or SUPABASE_KEY not set. "
                "Cannot initialize database client."
            )
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[DB] Supabase client initialized.")

    return _supabase_client


# =============================================================================
# SECTION 2: ERROR NOTIFICATIONS
# =============================================================================

def send_error_notification(error_message: str):
    """
    Sends a critical error alert via Telegram.
    Uses dedicated error bot if configured, falls back to main bot.
    Never raises — error notifications must not crash the caller.
    """
    try:
        token   = ERROR_BOT_TOKEN if ERROR_BOT_TOKEN else TELEGRAM_TOKEN
        chat_id = ERROR_CHAT_ID   if ERROR_CHAT_ID   else TELEGRAM_CHAT_ID

        if not token or not chat_id:
            logger.error(f"[Alert] No Telegram credentials. Error lost: {error_message}")
            return

        bot = telebot.TeleBot(token)
        bot.send_message(
            chat_id,
            f"🚨 *FUSION BOT CRITICAL ERROR* 🚨\n\n`{error_message}`",
            parse_mode="Markdown",
            timeout=10
        )
    except Exception as e:
        # Last resort: just log. Never let this function raise.
        logger.error(f"[Alert] Failed to send error notification: {e}")


# =============================================================================
# SECTION 3: FUSION SCORE CALCULATION
# =============================================================================

def calculate_fusion_score(
    sentiment: int,
    atr_multiplier: float,
    cot_bias: str,
    pair_direction: str
) -> int:
    """
    Master algorithm for trade viability scoring.

    Args:
        sentiment:       Integer from -10 to +10.
                         Positive = Bullish for the pair (EUR/USD goes UP).
                         Negative = Bearish for the pair (EUR/USD goes DOWN).
                         This is pair-specific — NOT USD strength.
        atr_multiplier:  Current candle TR / 14-period ATR.
        cot_bias:        One of: STRONGLY_BULLISH | BULLISH | NEUTRAL |
                                 BEARISH | STRONGLY_BEARISH
        pair_direction:  "LONG" or "SHORT"

    Returns:
        Integer score clamped to 0–100.

    Empirical note from 76-trade audit:
        Score 85 → 68.8% win rate (best tier)
        Score 100 → 40.0% win rate (worst tier)
        High score ≠ high quality. Do not chase Score 100 signals.
    """
    score = 50  # Neutral baseline

    # --- Weight 1: Volatility (ATR Expansion) ---
    if atr_multiplier >= 1.5:
        score += WEIGHT_ATR

    # --- Weight 2: Macro Sentiment (Directional Alignment) ---
    # sentiment > 0 means the pair price is expected to rise (Bullish for pair)
    # sentiment < 0 means the pair price is expected to fall (Bearish for pair)
    if pair_direction == "LONG":
        if sentiment > 0:
            score += WEIGHT_SENTIMENT
        elif sentiment < 0:
            score -= WEIGHT_SENTIMENT
    else:  # SHORT
        if sentiment < 0:
            score += WEIGHT_SENTIMENT
        elif sentiment > 0:
            score -= WEIGHT_SENTIMENT

    # --- Weight 3: COT Bias (5-State) ---
    # Full alignment: maximum boost
    # Partial alignment (NEUTRAL): no change
    # Opposition: penalty
    # Strong opposition: double penalty (validated by BULLISH_FADING thesis)
    if pair_direction == "LONG":
        if cot_bias == "STRONGLY_BULLISH":
            score += WEIGHT_COT + 5       # Extra confidence for strong alignment
        elif cot_bias == "BULLISH":
            score += WEIGHT_COT
        elif cot_bias == "NEUTRAL":
            pass                          # No adjustment
        elif cot_bias == "BEARISH":
            score -= WEIGHT_COT
        elif cot_bias == "STRONGLY_BEARISH":
            score -= WEIGHT_COT + 5       # Harder penalty for strong opposition
    else:  # SHORT
        if cot_bias == "STRONGLY_BEARISH":
            score += WEIGHT_COT + 5
        elif cot_bias == "BEARISH":
            score += WEIGHT_COT
        elif cot_bias == "NEUTRAL":
            pass
        elif cot_bias == "BULLISH":
            score -= WEIGHT_COT
        elif cot_bias == "STRONGLY_BULLISH":
            score -= WEIGHT_COT + 5

    return max(0, min(100, score))


# =============================================================================
# SECTION 4: SENTIMENT AGGREGATION
# This was the missing link in V5. sentiment_scanner.py was storing individual
# article sentiments in processed_sentiment but NEVER writing the aggregated
# score back to system_state.macro_sentiment. The Fusion Score was therefore
# always computing with sentiment = 0. This function fixes that.
# =============================================================================

def aggregate_and_push_sentiment(pair: str, lookback_hours: int = 6):
    """
    Reads recent processed_sentiment records for a pair, computes a net
    sentiment integer (-10 to +10), and writes it to system_state.

    Scoring logic:
        Each BULLISH record with HIGH importance   → +2
        Each BULLISH record with MEDIUM importance → +1
        Each BEARISH record with HIGH importance   → -2
        Each BEARISH record with MEDIUM importance → -1
        NEUTRAL records                            →  0
        Final score clamped to [-10, +10]

    Args:
        pair:           'EUR/USD' or 'GBP/USD'
        lookback_hours: How many hours of sentiment to aggregate (default 6)

    Called by: sentiment_scanner.py after each pipeline run.
    """
    try:
        from datetime import datetime, timezone, timedelta

        supabase = get_supabase_client()

        # Determine the correct column for this pair
        if pair == "EUR/USD":
            sentiment_col   = "eur_usd_sentiment"
            confidence_col  = "eur_usd_confidence"
        elif pair == "GBP/USD":
            sentiment_col   = "gbp_usd_sentiment"
            confidence_col  = "gbp_usd_confidence"
        else:
            logger.error(f"[Aggregator] Unknown pair: {pair}")
            return

        # Fetch recent records within the lookback window
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()

        response = supabase.table("processed_sentiment") \
            .select(f"{sentiment_col}, importance_tier") \
            .gte("created_at", cutoff) \
            .execute()

        records = response.data if response.data else []

        if not records:
            logger.info(f"[Aggregator] No recent sentiment for {pair}. Keeping current state.")
            return

        # Compute net score
        net = 0
        for record in records:
            sentiment  = record.get(sentiment_col, "NEUTRAL")
            tier       = record.get("importance_tier", "LOW")

            weight = 2 if tier == "HIGH" else (1 if tier == "MEDIUM" else 0)

            if sentiment == "BULLISH":
                net += weight
            elif sentiment == "BEARISH":
                net -= weight

        # Clamp to [-10, +10]
        net = max(-10, min(10, net))

        logger.info(f"[Aggregator] {pair} net sentiment: {net} (from {len(records)} records)")

        # Write to system_state — this is what Fusion Score reads
        supabase.table("system_state").upsert({
            "pair":            pair,
            "macro_sentiment": net,
            "last_updated":    datetime.now(timezone.utc).isoformat()
        }, on_conflict="pair").execute()

        logger.info(f"[Aggregator] system_state updated for {pair}: macro_sentiment = {net}")

    except Exception as e:
        logger.error(f"[Aggregator] Failed to aggregate sentiment for {pair}: {e}")
        send_error_notification(f"Sentiment Aggregation Failed ({pair}): {e}")
