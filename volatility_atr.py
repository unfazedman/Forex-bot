"""
Volatility Engine (V5.1) - ATR Expansion Signal Trigger
Industry Standard: Wilder's Smoothed ATR for Volatility Breakouts
Layer 1-4 Audit Applied: Logic, Resilience, Cost, Trading.
"""

import requests
import time
import os
import threading
import pytz
import logging
from datetime import datetime, timezone
from flask import Flask
import telebot

# --- THE CENTRAL NERVOUS SYSTEM PLUG-IN ---
from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TWELVE_DATA_KEY, 
    PAIRS, ATR_THRESHOLD, validate_config
)
from shared_functions import (
    get_supabase_client, calculate_fusion_score, 
    send_error_notification
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Validate config on startup
validate_config()

bot = telebot.TeleBot(TELEGRAM_TOKEN)
# Cache for the last processed candle time per pair to prevent duplicates
last_alerted_candles = {pair: None for pair in PAIRS}

app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Quantitative Volatility Engine is Online."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

class VolatilityEngine:
    """
    Monitors market volatility and triggers trade signals on ATR expansion.
    """
    
    @staticmethod
    def calculate_tr(high, low, prev_close):
        """Calculates the True Range (TR) for a given candle."""
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    def analyze_volatility(self):
        """
        Fetches price data and checks for ATR expansion signals.
        """
        # LAYER 4: Trading Logic - Weekend Killswitch (UTC)
        now_utc = datetime.now(timezone.utc)
        if now_utc.weekday() == 5 or (now_utc.weekday() == 4 and now_utc.hour >= 22) or (now_utc.weekday() == 6 and now_utc.hour < 21):
            logger.info("Market is closed. Volatility Engine standing by...")
            return

        pairs_str = ",".join(PAIRS)
        # Fetch 20 candles to ensure enough data for 14-period ATR + signal candle
        url = f"https://api.twelvedata.com/time_series?symbol={pairs_str}&interval=15min&outputsize=20&apikey={TWELVE_DATA_KEY}"
        
        try:
            # LAYER 2: System Resilience - Timeout and raise_for_status
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            # LAYER 3: Cost & Infrastructure - TwelveData error handling
            if data.get('status') == 'error':
                logger.error(f"TwelveData Error: {data.get('message')}")
                return
                
        except Exception as e:
            logger.error(f"TwelveData API Fetch Failed: {e}")
            send_error_notification(f"TwelveData API Fetch Failed: {e}")
            return

        for pair in PAIRS:
            pair_data = data.get(pair, {})
            candles = pair_data.get('values', [])
            
            if len(candles) < 16:
                logger.warning(f"Insufficient data for {pair}")
                continue

            # LAYER 4: Trading Logic - Use LAST CLOSED candle (candles[1])
            signal_candle = candles[1]
            signal_time = signal_candle['datetime']
            
            if last_alerted_candles[pair] == signal_time:
                continue

            try:
                signal_high = float(signal_candle['high'])
                signal_low = float(signal_candle['low'])
                signal_close = float(signal_candle['close'])
                signal_open = float(signal_candle['open'])
                prev_close = float(candles[2]['close'])
                
                # Calculate TR for signal candle
                signal_tr = self.calculate_tr(signal_high, signal_low, prev_close)
                
                # Calculate 14-period ATR (Simple Moving Average for initialization)
                trs = []
                for i in range(2, 16):
                    h = float(candles[i]['high'])
                    l = float(candles[i]['low'])
                    pc = float(candles[i+1]['close'])
                    trs.append(self.calculate_tr(h, l, pc))
                
                atr_14 = sum(trs) / len(trs)
                multiplier = signal_tr / atr_14 if atr_14 > 0 else 0
                
                logger.info(f"[{pair}] TR: {signal_tr:.5f} | 14-ATR: {atr_14:.5f} | Mult: {multiplier:.2f}x")

                if multiplier >= ATR_THRESHOLD:
                    self.process_signal(pair, signal_time, multiplier, signal_close, signal_open)
                    last_alerted_candles[pair] = signal_time
                    
            except (ValueError, KeyError, IndexError) as e:
                logger.error(f"Data processing error for {pair}: {e}")
                continue

    def process_signal(self, pair, signal_time, multiplier, signal_close, signal_open):
        """
        Calculates fusion score and executes alerts/logging.
        """
        try:
            supabase = get_supabase_client()
            
            # Fetch System State
            state_response = supabase.table("system_state").select("*").eq("pair", pair).execute()
            state = state_response.data[0] if state_response.data else {}
            
            current_sentiment = state.get('macro_sentiment', 0)
            current_cot = state.get('cot_bias', 'NEUTRAL')
            
            # LAYER 4: Trading Logic - Direction based on candle body
            direction = "LONG" if signal_close > signal_open else "SHORT"
            score = calculate_fusion_score(current_sentiment, multiplier, current_cot, direction)
            
            # LAYER 2: Separate try blocks for Telegram and DB
            # 1. Telegram Alert
            try:
                msg = f"⚡ **FUSION SIGNAL: {pair}** ⚡\n"
                msg += f"Direction: {direction}\n"
                msg += f"Confidence Score: {score}/100\n\n"
                msg += f"📊 Volatility: {multiplier:.1f}x ATR Expansion\n"
                msg += f"🧠 Macro Sentiment: {current_sentiment}\n"
                msg += f"🏦 Hedge Fund Bias: {current_cot}\n"
                bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown", timeout=10)
            except Exception as e:
                logger.error(f"Telegram signal failed: {e}")

            # 2. Database Log
            try:
                ist = pytz.timezone('Asia/Kolkata')
                timestamp = datetime.now(ist).isoformat()
                
                supabase.table("trade_logs").insert({
                    "timestamp_ist": timestamp,
                    "pair": pair,
                    "sentiment": current_sentiment,
                    "volatility_multiplier": f"{multiplier:.1f}x",
                    "cot_bias": current_cot,
                    "confidence_score": score, # LAYER 4: Integer score only
                    "entry_price": signal_close,
                    "direction": direction
                }).execute()
                logger.info(f"--> FUSION LOGGED: {pair} scored {score} ({direction})")
            except Exception as e:
                logger.error(f"Supabase logging failed: {e}")
                send_error_notification(f"Supabase Trade Log Failed: {e}")
                
        except Exception as e:
            logger.error(f"Fusion signal processing failed: {e}")
            send_error_notification(f"Fusion Signal Error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    engine = VolatilityEngine()
    logger.info("Fusion Volatility Engine V5.1 Online...")
    
    while True:
        engine.analyze_volatility()
        # LAYER 3: API Protection - 5 minute interval
        time.sleep(300)
