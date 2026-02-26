import telebot
import requests
from datetime import datetime, timezone, timedelta
import os

# 1. Securely load your credentials from GitHub's hidden vault
API_TOKEN = os.environ.get('8778002889:AAHWQLsPCN8YgdqMLdbs1TeZQ0nH5J1Fpzo')
CHAT_ID = os.environ.get('6027268088')

bot = telebot.TeleBot(API_TOKEN)

def fetch_and_send_news():
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    
    try:
        response = requests.get(url)
        data = response.json() 
        
        final_message = "🔴 🟠 High/Medium Impact News Today:\n\n"
        events_found = 0
        
        target_currencies = ['USD', 'EUR', 'GBP']
        target_impacts = ['High', 'Medium']
        ist_offset = timezone(timedelta(hours=5, minutes=30))
        
        # We only want TODAY's news, not the whole week
        today_date = datetime.now(ist_offset).date()
        
        for event in data:
            currency = event.get('country', '')
            impact = event.get('impact', '')
            
            if currency in target_currencies and impact in target_impacts:
                title = event.get('title', 'Unknown Event')
                raw_date_str = event.get('date', '') 
                
                try:
                    raw_time = datetime.fromisoformat(raw_date_str)
                    ist_time = raw_time.astimezone(ist_offset)
                    
                    # Filter: Only add the event if it happens today
                    if ist_time.date() == today_date:
                        clean_time = ist_time.strftime('%I:%M %p')
                        final_message += f"🌍 {currency} ({impact}) | ⏰ {clean_time} (IST)\n"
                        final_message += f"📌 {title}\n\n"
                        events_found += 1
                except Exception:
                    pass
        
        if events_found > 0:
            bot.send_message(CHAT_ID, final_message)
        else:
            bot.send_message(CHAT_ID, "No High or Medium impact news for EUR, GBP, or USD today.")
            
    except Exception as e:
        bot.send_message(CHAT_ID, f"Error fetching data: {e}")

# 2. Execute the function immediately when GitHub wakes up
if __name__ == "__main__":
    fetch_and_send_news()
      
