import requests
import time
import os
import threading
import pytz
from datetime import datetime, timezone
from flask import Flask
import telebot

# --- THE CENTRAL NERVOUS SYSTEM PLUG-IN ---
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TWELVE_DATA_KEY, PAIRS, ATR_THRESHOLD
from shared_functions import get_supabase_client, calculate_fusion_score, send_error_notification

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

def calculate_tr(high, low, prev_close):
    """Calculates the True Range (TR) for a given candle."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))

def calculate_wilder_atr(trs, prev_atr, n=14):
    """Calculates Wilder's Smoothed ATR."""
    if prev_atr == 0:
        return sum(trs) / len(trs) if trs else 0
    return ((prev_atr * (n - 1)) + trs[-1]) / n

def analyze_volatility():
    # WEEKEND KILLSWITCH (UTC)
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() == 5 or (now_utc.weekday() == 4 and now_utc.hour >= 22) or (now_utc.weekday() == 6 and now_utc.hour < 21):
        print("Market is closed. Volatility Engine standing by...")
        return

    pairs_str = ",".join(PAIRS)
    # Fetch 16 candles to have 14 for ATR + 1 for signal + 1 for prev_close
    url = f"https://api.twelvedata.com/time_series?symbol={pairs_str}&interval=15min&outputsize=20&apikey={TWELVE_DATA_KEY}"
    
    try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        error_msg = f"TwelveData API Fetch Failed: {str(e)}"
        print(error_msg)
        send_error_notification(error_msg)
        return

    for pair in PAIRS:
        pair_data = response.get(pair, {})
        if 'values' not in pair_data: 
            continue
            
        candles = pair_data['values']
        if len(candles) < 16: continue

        # --- FIX: USE COMPLETED CANDLE (candles[1]) ---
        # candles[0] is live/incomplete. candles[1] is the last fully closed 15m candle.
        signal_candle = candles[1]
        signal_time = signal_candle['datetime']
        
        # Prevent duplicate triggers for the same candle
        if last_alerted_candles[pair] == signal_time:
            continue

        signal_high = float(signal_candle['high'])
        signal_low = float(signal_candle['low'])
        signal_close = float(signal_candle['close'])
        signal_open = float(signal_candle['open'])
        prev_close = float(candles[2]['close'])
        
        # Calculate TR for the signal candle
        signal_tr = calculate_tr(signal_high, signal_low, prev_close)
        
        # Calculate ATR using Wilder's Smoothing (Approximate with last 14 closed candles)
        # trs for candles[2] to candles[15]
        trs = []
        for i in range(2, 16):
            h = float(candles[i]['high'])
            l = float(candles[i]['low'])
            pc = float(candles[i+1]['close'])
            trs.append(calculate_tr(h, l, pc))
        
        atr_14 = sum(trs) / len(trs) # Using simple average for initialization
        
        multiplier = signal_tr / atr_14 if atr_14 > 0 else 0
        print(f"[{pair}] Closed TR: {signal_tr:.5f} | 14-ATR: {atr_14:.5f} | Multiplier: {multiplier:.2f}x")

        if multiplier >= ATR_THRESHOLD:
            process_fusion_trigger(pair, signal_time, multiplier, signal_close, signal_open)
            last_alerted_candles[pair] = signal_time

def process_fusion_trigger(pair, signal_time, multiplier, signal_close, signal_open):
    try:
        supabase = get_supabase_client()
        
        # Fetch current state from Supabase
        state_response = supabase.table("system_state").select("*").eq("pair", pair).execute()
        if not state_response.data:
            print(f"No state found for {pair}")
            return
            
        state = state_response.data[0]
        current_sentiment = state.get('macro_sentiment', 0)
        current_cot = state.get('cot_bias', 'NEUTRAL')
        
        direction = "LONG" if signal_close > signal_open else "SHORT"
        score = calculate_fusion_score(current_sentiment, multiplier, current_cot, direction)
        
        # Alert via Telegram
        msg = f"⚡ **FUSION SIGNAL: {pair}** ⚡\n"
        msg += f"Direction: {direction}\n"
        msg += f"Confidence Score: {score}/100\n\n"
        msg += f"📊 Volatility: {multiplier:.1f}x ATR Expansion\n"
        msg += f"🧠 Macro Sentiment: {current_sentiment}\n"
        msg += f"🏦 Hedge Fund Bias: {current_cot}\n"
        bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown")

        # Log to Supabase trade_logs
        ist = pytz.timezone('Asia/Kolkata')
        timestamp = datetime.now(ist).isoformat()
        
        log_data = {
            "timestamp_ist": timestamp,
            "pair": pair,
            "sentiment": current_sentiment,
            "volatility_multiplier": f"{multiplier:.1f}x",
            "cot_bias": current_cot,
            "confidence_score": f"{score}/100",
            "entry_price": signal_close,
            "direction": direction
        }
        supabase.table("trade_logs").insert(log_data).execute()
        print(f"--> FUSION LOGGED: {pair} scored {score}/100 ({direction})")
        
    except Exception as e:
        error_msg = f"Fusion Processing Database Error for {pair}: {str(e)}"
        print(error_msg)
        send_error_notification(error_msg)

@bot.message_handler(commands=['status'])
def handle_status_command(message):
    try:
        supabase = get_supabase_client()
        state_response = supabase.table("system_state").select("*").eq("pair", "EUR/USD").execute()
        
        if state_response.data:
            state = state_response.data[0]
            eur_sent = state.get('macro_sentiment', 0)
            eur_cot = state.get('cot_bias', 'NEUTRAL')
            
            report = "🤖 **SYSTEM DIAGNOSTICS ONLINE** 🤖\n\n"
            report += "🟢 **Database:** SUPABASE CONNECTED\n"
            report += f"🧠 **Current Macro Score:** {eur_sent} (EUR/USD)\n"
            report += f"🏦 **Hedge Fund Bias:** {eur_cot} (EUR/USD)\n\n"
            report += f"📊 *Volatility Engine is hunting for >{ATR_THRESHOLD}x ATR expansions.*"
            bot.reply_to(message, report, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"System Error: {e}")

# ... (rest of the news command and main logic remains similar but updated with sleep 300)

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    # Note: run_telegram_listener would be here in a real deployment
    print("Fusion Engine V5.0 (Supabase) Started...")
    while True:
        analyze_volatility()
        # Standardized to 5 minutes (300s) to protect API limits
        time.sleep(300) 
