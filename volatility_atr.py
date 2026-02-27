import requests
import time
import telebot
import os
import threading
import json
import gspread
import pytz
from datetime import datetime, timezone
from flask import Flask

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
last_alerted_candles = {'EUR/USD': None, 'GBP/USD': None}

app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Quantitative Volatility Engine is Online."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def calculate_tr(high, low, prev_close):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))

def calculate_fusion_score(sentiment, atr_multiplier, cot_bias, pair_direction):
    score = 50 
    
    # 1. Volatility Weight 
    if atr_multiplier >= 1.5:
        score += 20
        
    # 2. Sentiment Weight (Is the daily macro news aligning?)
    # For EUR/USD: Bullish sentiment means Bullish USD (Bearish EUR).
    if pair_direction == "LONG":
        if sentiment <= -5: score += 15  # Bearish USD = Good for EUR LONG
        elif sentiment >= 5: score -= 15 # Bullish USD = Bad for EUR LONG
    else: # SHORT
        if sentiment >= 5: score += 15   # Bullish USD = Good for EUR SHORT
        elif sentiment <= -5: score -= 15
        
    # 3. Smart Money Weight
    if cot_bias == "BULLISH" and pair_direction == "LONG": score += 15
    elif cot_bias == "BEARISH" and pair_direction == "SHORT": score += 15
    elif cot_bias != "NEUTRAL": score -= 15 # Trading against hedge funds
        
    return max(0, min(100, score))

def analyze_volatility():
    url = f"https://api.twelvedata.com/time_series?symbol=EUR/USD,GBP/USD&interval=15min&outputsize=16&apikey={TWELVE_DATA_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    pairs = ['EUR/USD', 'GBP/USD']
    for pair in pairs:
        if 'values' not in response.get(pair, {}): continue
            
        candles = response[pair]['values']
        live_candle = candles[0]
        live_time = live_candle['datetime']
        live_high, live_low = float(live_candle['high']), float(live_candle['low'])
        prev_close = float(candles[1]['close'])
        
        live_tr = calculate_tr(live_high, live_low, prev_close)
        trs = [calculate_tr(float(candles[i]['high']), float(candles[i]['low']), float(candles[i+1]['close'])) for i in range(1, 15)]
        atr_14 = sum(trs) / len(trs)
        
        multiplier = live_tr / atr_14 if atr_14 > 0 else 0
        print(f"[{pair}] Live TR: {live_tr:.5f} | 14-ATR: {atr_14:.5f} | Multiplier: {multiplier:.2f}x")

        # THE QUANTITATIVE TRIGGER
        if multiplier >= 1.5:
            if last_alerted_candles[pair] != live_time:
                process_fusion_trigger(pair, live_time, multiplier, prev_close, live_candle)
                last_alerted_candles[pair] = live_time

def process_fusion_trigger(pair, live_time, multiplier, prev_close, live_candle):
    try:
        # 1. Open the Google Database securely
        creds_dict = json.loads(os.environ.get('GCP_CREDENTIALS'))
        gc = gspread.service_account_from_dict(creds_dict)
        state_sheet = gc.open("Quant Performance Log").worksheet("System State")
        log_sheet = gc.open("Quant Performance Log").sheet1
        
        # 2. Read the Central Brain (Row 2)
        state = state_sheet.row_values(2)
        eur_sent = int(state[0]) if len(state) > 0 and state[0].strip() else 0
        gbp_sent = int(state[1]) if len(state) > 1 and state[1].strip() else 0
        eur_cot = str(state[2]).upper() if len(state) > 2 else "NEUTRAL"
        gbp_cot = str(state[3]).upper() if len(state) > 3 else "NEUTRAL"

        # Match data to the specific pair
        current_sentiment = eur_sent if pair == 'EUR/USD' else gbp_sent
        current_cot = eur_cot if pair == 'EUR/USD' else gbp_cot
        
        # Determine direction of the spike (Bullish or Bearish candle)
        is_bullish_candle = float(live_candle['close']) > float(live_candle['open'])
        direction = "LONG" if is_bullish_candle else "SHORT"

        # 3. Calculate Fusion Score
        score = calculate_fusion_score(current_sentiment, multiplier, current_cot, direction)
        
        # 4. Send Advanced Telegram Alert
        msg = f"⚡ **FUSION SIGNAL: {pair}** ⚡\n"
        msg += f"Direction: {direction}\n"
        msg += f"Confidence Score: {score}/100\n\n"
        msg += f"📊 Volatility: {multiplier:.1f}x ATR Expansion\n"
        msg += f"🧠 Macro Sentiment: {current_sentiment}\n"
        msg += f"🏦 Hedge Fund Bias: {current_cot}\n"
        bot.send_message(CHAT_ID, msg, parse_mode="Markdown")

        # 5. Log directly to the spreadsheet
        ist = pytz.timezone('Asia/Kolkata')
        timestamp = datetime.now(ist).strftime('%Y-%m-%d %I:%M:%S %p')
        entry_price = float(live_candle['close'])
        
        log_sheet.append_row([timestamp, pair, current_sentiment, f"{multiplier:.1f}x", current_cot, f"{score}/100", entry_price])
        print(f"--> FUSION LOGGED: {pair} scored {score}/100")
        
    except Exception as e:
        print(f"Fusion Processing Error: {e}")
        bot.send_message(CHAT_ID, f"⚡ VOLATILITY SPIKE: {pair} (Fusion Engine Offline: {e})")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Fusion Engine V3.0 Started...")
    while True:
        analyze_volatility()
        time.sleep(300)
