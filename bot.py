"""
Forex Calendar Bot (V5.1) - High Impact News Alerting
Industry Standard: ForexFactory Calendar API
Layer 1-4 Audit Applied: Logic, Resilience, Cost, Trading.
"""

import telebot
import requests
import logging
from datetime import datetime, timezone, timedelta
from shared_functions import get_supabase_client, send_error_notification
from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, 
    ERROR_BOT_TOKEN, ERROR_CHAT_ID,
    validate_config
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Validate config on startup
validate_config()

class NewsBot:
    """
    Alerts on high/medium impact economic news for major currencies.
    """
    
    def __init__(self):
        try:
            self.bot = telebot.TeleBot(TELEGRAM_TOKEN)
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            send_error_notification(f"News Bot Init Failed: {e}")
            self.bot = None

    def fetch_calendar(self):
        """
        Fetches the latest economic calendar from ForexFactory (via FairEconomy).
        """
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        
        try:
            # LAYER 2: System Resilience - Timeout and raise_for_status
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if not isinstance(data, list):
                logger.error("Invalid data format from calendar API")
                return []
                
            return data
        except Exception as e:
            logger.error(f"Calendar API Error: {e}")
            send_error_notification(f"Calendar API Error: {e}")
            return []

    def run(self):
        """Processes the calendar and sends daily alerts."""
        if not self.bot:
            return

        data = self.fetch_calendar()
        if not data:
            return

        final_message = "🔴 🟠 **High/Medium Impact News Today:**\n\n"
        events_found = 0
        
        target_currencies = ['USD', 'EUR', 'GBP']
        target_impacts = ['High', 'Medium']
        
        # Standard IST Offset for Indian Market Focus
        ist_offset = timezone(timedelta(hours=5, minutes=30))
        today_date = datetime.now(ist_offset).date()
        
        for event in data:
            currency = event.get('country', '')
            impact = event.get('impact', '')
            
            # LAYER 1: Code Quality - Upstream validation
            if currency in target_currencies and impact in target_impacts:
                title = event.get('title', 'Unknown Event')
                raw_date_str = event.get('date', '') 
                
                try:
                    # LAYER 4: Trading Logic - Time-based windows
                    raw_time = datetime.fromisoformat(raw_date_str.replace('Z', '+00:00'))
                    ist_time = raw_time.astimezone(ist_offset)
                    
                    if ist_time.date() == today_date:
                        clean_time = ist_time.strftime('%I:%M %p')
                        final_message += f"🌍 {currency} ({impact}) | ⏰ {clean_time} (IST)\n"
                        final_message += f"📌 {title}\n\n"
                        events_found += 1
                except Exception as e:
                    logger.warning(f"Error parsing date {raw_date_str}: {e}")
                    continue
        
        # LAYER 2: Separate try block for Telegram
        try:
            if events_found > 0:
                self.bot.send_message(
                    TELEGRAM_CHAT_ID, 
                    final_message, 
                    parse_mode="Markdown",
                    timeout=10
                )
                logger.info(f"Sent {events_found} news alerts to Telegram")
            else:
                self.bot.send_message(
                    TELEGRAM_CHAT_ID, 
                    "✅ No High or Medium impact news for EUR, GBP, or USD today.",
                    timeout=10
                )
                logger.info("No relevant news today.")
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")
            send_error_notification(f"Telegram News Alert Failed: {e}")

if __name__ == "__main__":
    bot_instance = NewsBot()
    bot_instance.run()
