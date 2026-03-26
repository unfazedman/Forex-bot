import requests
import time
import telebot
import os
import threading
import pytz
from datetime import datetime, timezone
from flask import Flask

# --- THE CENTRAL NERVOUS SYSTEM PLUG-IN ---
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TWELVE_DATA_KEY, PAIRS, ATR_THRESHOLD, SHEET_NAME, STATE_TAB, LOG_TAB
from shared_functions import get_gspread_client, calculate_fusion_score, send_error_notification


bot = telebot.TeleBot(TELEGRAM_TOKEN)
last_alerted_candles = {pair: None for pair in PAIRS}

app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Quantitative Volatility Engine is Online."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def calculate_tr(high, low, prev_close):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))

def analyze_volatility():
    # WEEKEND KILLSWITCH
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() == 5 or (now_utc.weekday() == 4 and now_utc.hour >= 22) or (now_utc.weekday() == 6 and now_utc.hour < 21):
        print("Market is closed. Volatility Engine standing by...")
        return

    # Dynamically pull the pairs from config.py
    pairs_str = ",".join(PAIRS)
    url = f"https://api.twelvedata.com/time_series?symbol={pairs_str}&interval=15min&outputsize=16&apikey={TWELVE_DATA_KEY}"
        try:
        response = requests.get(url, timeout=10).json()
    except Exception as e:
        error_msg = f"TwelveData API Fetch Failed: {str(e)}"
        print(error_msg)
        send_error_notification(error_msg)
        return


    for pair in PAIRS:
        if 'values' not in response.get(pair, {}): continue
            
        candles = response[pair]['values']
        live_candle = candles[0]
        live_time = live_candle['datetime']
        live_high, live_low = float(live_candle['high']), float(live_candle['low'])
        prev_close = float(candles[1]['close'])
        
        live_tr = calculate_tr(live_high, live_low, prev_close)
        trs = [calculate_tr(float(candles[i]['high']), float(candles[i]['low']), float(candles[i+1]['close'])) for i in range(1, 15)]
        atr_14 = sum(trs) / len(trs) if len(trs) > 0 else 0
        
        multiplier = live_tr / atr_14 if atr_14 > 0 else 0
        print(f"[{pair}] Live TR: {live_tr:.5f} | 14-ATR: {atr_14:.5f} | Multiplier: {multiplier:.2f}x")

        # Dynamically checks the threshold set in config.py
        if multiplier >= ATR_THRESHOLD:
            if last_alerted_candles[pair] != live_time:
                process_fusion_trigger(pair, live_time, multiplier, prev_close, live_candle)
                last_alerted_candles[pair] = live_time

def process_fusion_trigger(pair, live_time, multiplier, prev_close, live_candle):
    try:
        # Securely grabs the authentication client from shared_functions.py
        gc = get_gspread_client()
        state_sheet = gc.open(SHEET_NAME).worksheet(STATE_TAB)
        log_sheet = gc.open(SHEET_NAME).worksheet(LOG_TAB)
        
        state = state_sheet.row_values(2)
        eur_sent = int(state[0]) if len(state) > 0 and state[0].strip() else 0
        gbp_sent = int(state[1]) if len(state) > 1 and state[1].strip() else 0
        eur_cot = str(state[2]).upper() if len(state) > 2 else "NEUTRAL"
        gbp_cot = str(state[3]).upper() if len(state) > 3 else "NEUTRAL"

        current_sentiment = eur_sent if pair == 'EUR/USD' else gbp_sent
        current_cot = eur_cot if pair == 'EUR/USD' else gbp_cot
        
        is_bullish_candle = float(live_candle['close']) > float(live_candle['open'])
        direction = "LONG" if is_bullish_candle else "SHORT"

        # Calculates score using the single source of truth in shared_functions.py
        score = calculate_fusion_score(current_sentiment, multiplier, current_cot, direction)
        
        msg = f"⚡ **FUSION SIGNAL: {pair}** ⚡\n"
        msg += f"Direction: {direction}\n"
        msg += f"Confidence Score: {score}/100\n\n"
        msg += f"📊 Volatility: {multiplier:.1f}x ATR Expansion\n"
        msg += f"🧠 Macro Sentiment: {current_sentiment}\n"
        msg += f"🏦 Hedge Fund Bias: {current_cot}\n"
        bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown")

        ist = pytz.timezone('Asia/Kolkata')
        timestamp = datetime.now(ist).strftime('%Y-%m-%d %I:%M:%S %p')
        entry_price = float(live_candle['close'])
        
        log_sheet.append_row([
            timestamp, 
            pair, 
            current_sentiment, 
            f"{multiplier:.1f}x", 
            current_cot, 
            f"{score}/100", 
            entry_price,
            direction
        ])
        print(f"--> FUSION LOGGED: {pair} scored {score}/100 ({direction})")
        
        except Exception as e:
        error_msg = f"Fusion Processing Database Error for {pair}: {str(e)}"
        print(error_msg)
        send_error_notification(error_msg)

@bot.message_handler(commands=['status'])
def handle_status_command(message):
    try:
        gc = get_gspread_client()
        state = gc.open(SHEET_NAME).worksheet(STATE_TAB).row_values(2)
        
        eur_sent = int(state[0]) if len(state) > 0 else 0
        eur_cot = str(state[2]).upper() if len(state) > 2 else "NEUTRAL"
        
        report = "🤖 **SYSTEM DIAGNOSTICS ONLINE** 🤖\n\n"
        report += "🟢 **Render Server:** AWAKE & SCANNING\n"
        report += f"🧠 **Current Macro Score:** {eur_sent} (EUR/USD)\n"
        report += f"🏦 **Hedge Fund Bias:** {eur_cot} (EUR/USD)\n\n"
        report += f"📊 *Volatility Engine is hunting for >{ATR_THRESHOLD}x ATR expansions.*"
        
        bot.reply_to(message, report, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"System Error: {e}")

@bot.message_handler(commands=['news'])
def handle_news_command(message):
    try:
        loading_msg = bot.reply_to(message, "⏳ Fetching live economic calendar...", parse_mode="Markdown")
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        
        # Kept the Cloudflare bypass intact
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        calendar_data = requests.get(url, headers=headers, timeout=10).json()
        
        ist = pytz.timezone('Asia/Kolkata')
        today_date = datetime.now(ist).strftime('%Y-%m-%d')
        report = "🔴🟠 **TODAY'S HIGH/MEDIUM IMPACT NEWS** 🔴🟠\n\n"
        has_news = False
        
        for event in calendar_data:
            event_date = event['date'][:10]
            if event_date == today_date and event['country'] in ['USD', 'EUR', 'GBP']:
                if event['impact'] in ['High', 'Medium']:
                    impact_emoji = "🔴" if event['impact'] == 'High' else "🟠"
                    utc_time = datetime.strptime(event['date'], "%Y-%m-%dT%H:%M:%S%z")
                    ist_time = utc_time.astimezone(ist).strftime('%I:%M %p')
                    
                    report += f"🌍 **{event['country']} ({event['impact']})** | ⏰ {ist_time} (IST)\n"
                    report += f"📌 {event['title']}\n\n"
                    has_news = True
        
        if not has_news:
            report += "No major structural news for EUR, GBP, or USD for the rest of the day."
            
        bot.delete_message(message.chat.id, loading_msg.message_id)
        bot.send_message(message.chat.id, report, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(message.chat.id, f"Error fetching calendar: {e}")

def run_telegram_listener():
    print("Telegram Listener Started...")
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=run_telegram_listener, daemon=True).start()
    print("Fusion Engine V4.0 Started...")
    while True:
        analyze_volatility()
        # THE FIX: Tightened the execution loop from 300 seconds to 60 seconds.
        time.sleep(60) 
