import requests
import time
import telebot
import os
import threading
from flask import Flask

# 1. Load from Cloud Vault
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
last_alerted_candles = {'EUR/USD': None, 'GBP/USD': None}

# 2. The Dummy Web Server (To keep Render happy)
app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Quantitative Volatility Engine is Online."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# 3. The Math Engine
def calculate_tr(high, low, prev_close):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))

def analyze_volatility():
    url = f"https://api.twelvedata.com/time_series?symbol=EUR/USD,GBP/USD&interval=15min&outputsize=16&apikey={TWELVE_DATA_KEY}"
    try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        print(f"API Error: {e}")
        return

    pairs = ['EUR/USD', 'GBP/USD']
    for pair in pairs:
        if 'values' not in response.get(pair, {}):
            continue
            
        candles = response[pair]['values']
        live_candle = candles[0]
        live_time = live_candle['datetime']
        live_high, live_low = float(live_candle['high']), float(live_candle['low'])
        prev_close = float(candles[1]['close'])
        
        live_tr = calculate_tr(live_high, live_low, prev_close)
        
        trs = [calculate_tr(float(candles[i]['high']), float(candles[i]['low']), float(candles[i+1]['close'])) for i in range(1, 15)]
        atr_14 = sum(trs) / len(trs)
        
        print(f"[{pair}] Live TR: {live_tr:.5f} | 14-ATR: {atr_14:.5f}")

        if live_tr >= (atr_14 * 1.5):
            if last_alerted_candles[pair] != live_time:
                bot.send_message(CHAT_ID, f"⚡ VOLATILITY SPIKE: {pair}\nLive TR has expanded to 1.5x normal ATR.\n\nLive TR: {live_tr:.5f}\nNormal ATR: {atr_14:.5f}")
                last_alerted_candles[pair] = live_time

if __name__ == "__main__":
    # Start the dummy web server in the background
    threading.Thread(target=run_web, daemon=True).start()
    
    print("Cloud Engine V2.0 Started...")
    while True:
        analyze_volatility()
        time.sleep(300)
