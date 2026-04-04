"""
volatility_atr.py - Volatility Engine & Fusion Signal Trigger
Fusion Score Bot V6.0

Runs on Render (always-on process).
Monitors EUR/USD and GBP/USD every 5 minutes for ATR expansion signals.
On signal: calculates Fusion Score, fires Telegram alert, logs to Supabase.

Key fixes from V5 audit:
    - last_alerted_candles persisted to Supabase (not in-memory only)
    - Supabase client is singleton from shared_functions (not per-signal)
    - Doji candle edge case handled (close == open → no trade)
    - Flask watchdog: if engine loop dies, web endpoint reports it
    - candles[1] for signal, candles[2] for prev_close (live candle bug fixed)
    - ATR loop bounds verified: range(2,16) with candles[i+1] → max index 16,
      safe with outputsize=20
"""

import time
import os
import threading
import pytz
import logging
import requests
from datetime import datetime, timezone
from flask import Flask
import telebot

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    TWELVE_DATA_KEY, PAIRS, ATR_THRESHOLD,
    validate_config
)
from shared_functions import (
    get_supabase_client,
    calculate_fusion_score,
    send_error_notification
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# FLASK KEEPALIVE (Render free tier)
# The /health endpoint reports engine status so external pingers can detect
# if the engine loop has silently died while Flask stays alive.
# =============================================================================

app = Flask(__name__)

# Shared state between Flask and engine threads
_engine_status = {
    "alive":      True,
    "last_cycle": None,   # ISO timestamp of last completed analyze_volatility()
    "errors":     0
}

@app.route('/')
def keep_alive():
    return "Fusion Volatility Engine V6.0 Online."

@app.route('/health')
def health():
    """
    Returns engine health for external monitoring.
    If last_cycle is None or > 10 minutes ago, engine may be stuck.
    """
    status = "OK" if _engine_status["alive"] else "DEAD"
    return {
        "status":     status,
        "last_cycle": _engine_status["last_cycle"],
        "errors":     _engine_status["errors"]
    }, 200 if _engine_status["alive"] else 503

def _run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)


# =============================================================================
# VOLATILITY ENGINE
# =============================================================================

class VolatilityEngine:
    """
    Monitors market volatility and triggers Fusion Score signals on ATR expansion.
    """

    def __init__(self):
        try:
            validate_config('volatility_atr')
            # Singleton client — not recreated per signal
            self.supabase = get_supabase_client()
            self.bot      = telebot.TeleBot(TELEGRAM_TOKEN)
            logger.info("[Engine] Initialized successfully.")
        except Exception as e:
            logger.error(f"[Engine] Initialization failed: {e}")
            send_error_notification(f"Volatility Engine Init Failed: {e}")
            self.supabase = None
            self.bot      = None

        # In-memory cache seeded from Supabase on startup
        # Prevents duplicate signals across the current session
        self.last_alerted_candles = {pair: None for pair in PAIRS}
        self._seed_alerted_candles()

    # =========================================================================
    # DEDUPLICATION — persisted to Supabase
    # =========================================================================

    def _seed_alerted_candles(self):
        """
        On startup, reads last alerted candle time per pair from Supabase.
        Prevents duplicate signals after Render restarts.

        V5 bug: last_alerted_candles was module-level in-memory only.
        On restart, it reset to None, allowing the most recent closed candle
        to fire again as a new signal.
        """
        if not self.supabase:
            return

        try:
            for pair in PAIRS:
                response = (
                    self.supabase
                    .table("system_state")
                    .select("last_alerted_candle")
                    .eq("pair", pair)
                    .execute()
                )
                if response.data and response.data[0].get('last_alerted_candle'):
                    self.last_alerted_candles[pair] = (
                        response.data[0]['last_alerted_candle']
                    )
                    logger.info(
                        f"[Dedup] Seeded {pair} last alerted: "
                        f"{self.last_alerted_candles[pair]}"
                    )
        except Exception as e:
            logger.warning(f"[Dedup] Could not seed alerted candles: {e}")

    def _persist_alerted_candle(self, pair: str, candle_time: str):
        """Saves the alerted candle time to Supabase for restart persistence."""
        if not self.supabase:
            return

        try:
            self.supabase.table("system_state").upsert({
                "pair":                 pair,
                "last_alerted_candle":  candle_time,
            }, on_conflict="pair").execute()
        except Exception as e:
            logger.error(f"[Dedup] Failed to persist alerted candle for {pair}: {e}")

    # =========================================================================
    # ATR CALCULATION
    # =========================================================================

    @staticmethod
    def _calculate_tr(high: float, low: float, prev_close: float) -> float:
        """
        True Range: max of (H-L), |H-PC|, |L-PC|.
        Wilder's definition — industry standard.
        """
        return max(
            high - low,
            abs(high - prev_close),
            abs(low  - prev_close)
        )

    def _calculate_atr(self, candles: list) -> tuple:
        """
        Calculates signal candle TR and 14-period ATR.

        Candle index convention (TwelveData returns newest first):
            candles[0]  = live / forming candle  → NEVER use for signals
            candles[1]  = last CLOSED candle      → signal candle
            candles[2]  = candle before signal    → prev_close for signal TR
            candles[3..16] = 14 candles for ATR   → loop range(2, 16)
                             each uses candles[i+1] as prev_close
                             max index: candles[16] → safe with outputsize=20

        Returns:
            (signal_tr, atr_14, signal_candle_data)
            Returns (None, None, None) on error.
        """
        try:
            signal_candle = candles[1]
            signal_high   = float(signal_candle['high'])
            signal_low    = float(signal_candle['low'])
            signal_close  = float(signal_candle['close'])
            signal_open   = float(signal_candle['open'])
            prev_close    = float(candles[2]['close'])

            signal_tr = self._calculate_tr(signal_high, signal_low, prev_close)

            # 14-period ATR as SMA of TR
            trs = []
            for i in range(2, 16):
                h  = float(candles[i]['high'])
                l  = float(candles[i]['low'])
                pc = float(candles[i + 1]['close'])
                trs.append(self._calculate_tr(h, l, pc))

            atr_14 = sum(trs) / len(trs) if trs else 0

            candle_data = {
                "time":  signal_candle['datetime'],
                "high":  signal_high,
                "low":   signal_low,
                "close": signal_close,
                "open":  signal_open
            }

            return signal_tr, atr_14, candle_data

        except (ValueError, KeyError, IndexError) as e:
            logger.error(f"[ATR] Calculation error: {e}")
            return None, None, None

    # =========================================================================
    # MARKET HOURS
    # =========================================================================

    @staticmethod
    def _market_is_open() -> bool:
        """
        Returns False during weekend market closure.
        Forex closes Friday 22:00 UTC, opens Sunday 21:00 UTC.
        """
        now = datetime.now(timezone.utc)
        weekday = now.weekday()   # 0=Mon, 4=Fri, 5=Sat, 6=Sun

        if weekday == 5:
            return False   # All Saturday
        if weekday == 4 and now.hour >= 22:
            return False   # Friday after 22:00 UTC
        if weekday == 6 and now.hour < 21:
            return False   # Sunday before 21:00 UTC

        return True

    # =========================================================================
    # SIGNAL PROCESSING
    # =========================================================================

    def _determine_direction(self, open_price: float, close_price: float) -> str:
        """
        Determines trade direction from candle body.

        V5 bug: doji candles (close == open) were classified as SHORT.
        Now returns None for doji — no signal on ambiguous candles.

        Returns:
            "LONG", "SHORT", or None (doji — skip signal)
        """
        diff = close_price - open_price

        # Doji: body smaller than 0.1 pip (0.00001)
        if abs(diff) < 0.00001:
            logger.info("[Signal] Doji candle detected. Skipping signal.")
            return None

        return "LONG" if diff > 0 else "SHORT"

    def _fetch_system_state(self, pair: str) -> dict:
        """
        Reads macro_sentiment and cot_bias from system_state for a pair.
        Returns defaults if unavailable.
        """
        defaults = {"macro_sentiment": 0, "cot_bias": "NEUTRAL"}

        if not self.supabase:
            return defaults

        try:
            response = (
                self.supabase
                .table("system_state")
                .select("macro_sentiment, cot_bias")
                .eq("pair", pair)
                .execute()
            )
            if response.data:
                state = response.data[0]
                return {
                    "macro_sentiment": state.get('macro_sentiment', 0) or 0,
                    "cot_bias":        state.get('cot_bias', 'NEUTRAL') or 'NEUTRAL'
                }
        except Exception as e:
            logger.error(f"[State] Failed to fetch system state for {pair}: {e}")

        return defaults

    def _send_signal_alert(self, pair: str, direction: str, score: int,
                           multiplier: float, sentiment: int, cot: str):
        """Sends Fusion Score signal to Telegram."""
        if not self.bot:
            return

        score_emoji = "🔥" if score >= 80 else ("⚡" if score >= 65 else "📊")
        dir_emoji   = "📈" if direction == "LONG" else "📉"

        msg  = f"{score_emoji} *FUSION SIGNAL: {pair}* {score_emoji}\n\n"
        msg += f"{dir_emoji} Direction: *{direction}*\n"
        msg += f"🎯 Confidence Score: *{score}/100*\n\n"
        msg += f"📊 Volatility: `{multiplier:.2f}x` ATR Expansion\n"
        msg += f"🧠 Macro Sentiment: `{sentiment:+d}`\n"
        msg += f"🏦 COT Bias: `{cot}`\n"

        try:
            self.bot.send_message(
                TELEGRAM_CHAT_ID, msg,
                parse_mode="Markdown",
                timeout=10
            )
        except Exception as e:
            logger.error(f"[Alert] Telegram send failed: {e}")

    def _log_trade_to_db(self, pair: str, direction: str, score: int,
                         multiplier: float, sentiment: int, cot: str,
                         entry_price: float, signal_time: str):
        """Logs the trade signal to Supabase trade_logs table."""
        if not self.supabase:
            return

        ist = pytz.timezone('Asia/Kolkata')
        timestamp_ist = datetime.now(ist).isoformat()

        try:
            self.supabase.table("trade_logs").insert({
                "timestamp_ist":       timestamp_ist,
                "pair":                pair,
                "direction":           direction,
                "confidence_score":    score,        # INT — schema fixed
                "sentiment":           sentiment,
                "volatility_multiplier": round(multiplier, 2),
                "cot_bias":            cot,
                "entry_price":         entry_price,
            }).execute()
            logger.info(f"[DB] Trade logged: {pair} {direction} score={score}")
        except Exception as e:
            logger.error(f"[DB] Trade log failed: {e}")
            send_error_notification(f"Trade Log Failed ({pair}): {e}")

    def _process_signal(self, pair: str, candle: dict, multiplier: float):
        """
        Full signal processing pipeline:
        1. Determine direction (skip doji)
        2. Fetch system state (sentiment + COT)
        3. Calculate Fusion Score
        4. Send Telegram alert
        5. Log to Supabase
        6. Persist alerted candle time
        """
        direction = self._determine_direction(candle['open'], candle['close'])

        if direction is None:
            return   # Doji — no signal

        state     = self._fetch_system_state(pair)
        sentiment = state['macro_sentiment']
        cot       = state['cot_bias']

        score = calculate_fusion_score(sentiment, multiplier, cot, direction)

        logger.info(
            f"[Signal] {pair} {direction} | Score: {score} | "
            f"ATR: {multiplier:.2f}x | Sentiment: {sentiment} | COT: {cot}"
        )

        # Telegram alert (separate try — DB failure shouldn't suppress alert)
        try:
            self._send_signal_alert(
                pair, direction, score, multiplier, sentiment, cot
            )
        except Exception as e:
            logger.error(f"[Signal] Alert failed: {e}")

        # DB log (separate try — alert failure shouldn't suppress DB write)
        try:
            self._log_trade_to_db(
                pair, direction, score, multiplier,
                sentiment, cot, candle['close'], candle['time']
            )
        except Exception as e:
            logger.error(f"[Signal] DB log failed: {e}")

        # Persist dedup marker to survive Render restarts
        self.last_alerted_candles[pair] = candle['time']
        self._persist_alerted_candle(pair, candle['time'])

    # =========================================================================
    # MAIN ANALYSIS LOOP
    # =========================================================================

    def analyze_volatility(self):
        """
        Fetches latest candles for all pairs and checks for ATR expansion.
        Called every 5 minutes from the main loop.
        """
        if not self._market_is_open():
            logger.info("[Engine] Market closed. Standing by.")
            return

        pairs_str = ",".join(PAIRS)
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={pairs_str}&interval=15min&outputsize=20"
            f"&apikey={TWELVE_DATA_KEY}"
        )

        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()

            # TwelveData API-level error (e.g. wrong key, quota exceeded)
            if isinstance(data, dict) and data.get('status') == 'error':
                logger.error(f"[API] TwelveData error: {data.get('message')}")
                send_error_notification(f"TwelveData API Error: {data.get('message')}")
                return

        except Exception as e:
            logger.error(f"[API] TwelveData fetch failed: {e}")
            send_error_notification(f"TwelveData Fetch Failed: {e}")
            return

        for pair in PAIRS:
            pair_data = data.get(pair, {})

            # Handle per-pair API error
            if isinstance(pair_data, dict) and pair_data.get('status') == 'error':
                logger.error(
                    f"[API] TwelveData error for {pair}: {pair_data.get('message')}"
                )
                continue

            candles = pair_data.get('values', [])

            if len(candles) < 17:
                logger.warning(
                    f"[Engine] Insufficient data for {pair}: "
                    f"{len(candles)} candles (need 17)."
                )
                continue

            signal_time = candles[1]['datetime']

            # Skip if we already alerted on this candle
            if self.last_alerted_candles[pair] == signal_time:
                logger.info(f"[Dedup] {pair}: already alerted on {signal_time}.")
                continue

            signal_tr, atr_14, candle = self._calculate_atr(candles)

            if signal_tr is None:
                continue

            multiplier = signal_tr / atr_14 if atr_14 > 0 else 0

            logger.info(
                f"[{pair}] TR: {signal_tr:.5f} | "
                f"ATR-14: {atr_14:.5f} | "
                f"Mult: {multiplier:.2f}x"
            )

            if multiplier >= ATR_THRESHOLD:
                self._process_signal(pair, candle, multiplier)

        # Update watchdog timestamp
        _engine_status["last_cycle"] = datetime.now(timezone.utc).isoformat()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    validate_config('volatility_atr')

    # Start Flask in background thread (keeps Render process alive)
    threading.Thread(target=_run_web, daemon=True).start()
    logger.info("[Engine] Flask keepalive started.")

    engine = VolatilityEngine()
    logger.info("[Engine] Fusion Volatility Engine V6.0 starting main loop...")

    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 5

    while True:
        try:
            engine.analyze_volatility()
            consecutive_errors = 0
            _engine_status["alive"] = True

        except Exception as e:
            consecutive_errors += 1
            _engine_status["errors"] += 1
            logger.error(f"[Engine] Loop error #{consecutive_errors}: {e}")

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                _engine_status["alive"] = False
                send_error_notification(
                    f"Volatility Engine: {consecutive_errors} consecutive "
                    f"errors. Last error: {e}"
                )
                # Reset counter so we don't spam error notifications
                consecutive_errors = 0

        time.sleep(300)   # 5-minute interval — protects TwelveData 800 credits/day
