import requests
import telebot
import os
import json
import gspread

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def update_central_brain(eur_bias, gbp_bias):
    try:
        creds_dict = json.loads(os.environ.get('GCP_CREDENTIALS'))
        gc = gspread.service_account_from_dict(creds_dict)
        sheet = gc.open("Quant Performance Log").worksheet("System State")
        
        sheet.update_acell('C2', eur_bias)
        sheet.update_acell('D2', gbp_bias)
        print("\n--> Central Brain Updated with fresh COT Data!")
    except Exception as e:
        print(f"\nFailed to update Central Brain: {e}")

def get_smart_money_data(asset_name):
    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    params = {"market_and_exchange_names": asset_name, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1}
    
    try:
        response = requests.get(url, params=params, timeout=10).json()
        if response:
            latest = response[0]
            longs = int(latest.get('noncomm_positions_long_all', 0))
            shorts = int(latest.get('noncomm_positions_short_all', 0))
            return latest.get('report_date_as_yyyy_mm_dd', 'Unknown')[:10], longs, shorts, longs - shorts
    except Exception as e:
        print(f"API Error fetching {asset_name}: {e}")
    return None, 0, 0, 0

def generate_cot_report():
    assets = {"EUR": "EURO FX - CHICAGO MERCANTILE EXCHANGE", "GBP": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE"}
    report_text = "🏦 **SMART MONEY TRACKER (COT)** 🏦\n\n"
    
    eur_final_bias = "NEUTRAL"
    gbp_final_bias = "NEUTRAL"
    
    for currency, market in assets.items():
        date, longs, shorts, net = get_smart_money_data(market)
        if date:
            bias = "BULLISH" if net > 0 else "BEARISH"
            if currency == "EUR": eur_final_bias = bias
            if currency == "GBP": gbp_final_bias = bias
            
            report_text += f"*{currency} Bias:* 🟢 {bias}\n" if bias == "BULLISH" else f"*{currency} Bias:* 🔴 {bias}\n"
            report_text += f"⚖️ **Net Position: {net:,}**\n\n"

    try:
        bot.send_message(CHAT_ID, report_text, parse_mode="Markdown")
        update_central_brain(eur_final_bias, gbp_final_bias)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    generate_cot_report()
    
