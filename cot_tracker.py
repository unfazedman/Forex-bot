import requests
import telebot

# 1. Your Digital Vault
TELEGRAM_TOKEN = 'YOUR_TELEGRAM_TOKEN'
CHAT_ID = 'YOUR_CHAT_ID'

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def get_smart_money_data(asset_name):
    # 2. The US Government CFTC API (Financial Futures)
    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    
    # We ask the API to only give us the single newest row for our specific currency
    params = {
        "market_and_exchange_names": asset_name,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 1
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data:
            latest = data[0]
            date = latest.get('report_date_as_yyyy_mm_dd', 'Unknown')[:10]
            
            # Extract Hedge Fund (Non-Commercial) Longs and Shorts
            longs = int(latest.get('noncomm_positions_long_all', 0))
            shorts = int(latest.get('noncomm_positions_short_all', 0))
            
            # The Quant Math: Net Positioning
            net_position = longs - shorts
            return date, longs, shorts, net_position
            
    except Exception as e:
        print(f"API Error fetching {asset_name}: {e}")
        
    return None, 0, 0, 0

def generate_cot_report():
    print("Connecting to US CFTC Database...\n")
    
    # The exact strings the US Government uses for these currencies
    assets = {
        "EUR": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
        "GBP": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE"
    }
    
    report_text = "🏦 **SMART MONEY TRACKER (COT)** 🏦\n\n"
    
    for currency, market in assets.items():
        date, longs, shorts, net = get_smart_money_data(market)
        
        if date:
            # Determine the structural bias
            bias = "🟢 BULLISH" if net > 0 else "🔴 BEARISH"
            
            report_text += f"*{currency} Bias:* {bias}\n"
            report_text += f"📅 Data Date: {date}\n"
            report_text += f"📈 Hedge Fund Longs: {longs:,}\n"
            report_text += f"📉 Hedge Fund Shorts: {shorts:,}\n"
            report_text += f"⚖️ **Net Position: {net:,}**\n\n"
            
            print(f"Successfully processed {currency} for {date}.")
        else:
            print(f"Failed to pull data for {currency}.")

    # 3. Send to Telegram
    try:
        bot.send_message(CHAT_ID, report_text, parse_mode="Markdown")
        print("\n--> COT Report successfully sent to Telegram!")
    except Exception as e:
        print(f"Telegram Error: {e}")

if __name__ == "__main__":
    generate_cot_report()
      
