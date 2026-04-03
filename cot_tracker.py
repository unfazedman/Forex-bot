"""
COT Tracker (V5.1) - Smart Money Sentiment Analysis
Industry Standard: CFTC Commitments of Traders (COT) Analysis
Layer 1-4 Audit Applied: Logic, Resilience, Cost, Trading.
"""

import requests
import telebot
import os
import logging
from datetime import datetime, timezone
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

class COTTracker:
    """
    Tracks institutional positioning via CFTC Public Reporting API.
    """
    
    def __init__(self):
        try:
            self.supabase = get_supabase_client()
            self.bot = telebot.TeleBot(TELEGRAM_TOKEN)
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            send_error_notification(f"COT Tracker Init Failed: {e}")
            self.supabase = None
            self.bot = None

    def get_cftc_data(self, market_name: str):
        """
        Fetches latest non-commercial positioning from CFTC Socrata API.
        Industry Standard: Non-Commercial (Speculative) Net Positions.
        """
        # 2026 API Endpoint for TFF (Traders in Financial Futures)
        url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
        params = {
            "market_and_exchange_names": market_name, 
            "$order": "report_date_as_yyyy_mm_dd DESC", 
            "$limit": 1
        }
        
        try:
            # LAYER 2: System Resilience - Timeout and raise_for_status
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data:
                latest = data[0]
                # Non-Commercial Positions (Smart Money)
                longs = int(latest.get('noncomm_positions_long_all', 0))
                shorts = int(latest.get('noncomm_positions_short_all', 0))
                net = longs - shorts
                date = latest.get('report_date_as_yyyy_mm_dd', 'Unknown')[:10]
                
                logger.info(f"Fetched COT for {market_name}: Net {net}")
                return date, longs, shorts, net
        except Exception as e:
            logger.error(f"CFTC API Error for {market_name}: {e}")
            send_error_notification(f"COT API Error ({market_name}): {e}")
            
        return None, 0, 0, 0

    def update_system_state(self, pair: str, bias: str):
        """Updates the global system state in Supabase."""
        if not self.supabase:
            return
            
        try:
            # LAYER 1: Code Quality - Upstream validation check
            if bias not in ["BULLISH", "BEARISH", "NEUTRAL"]:
                logger.warning(f"Invalid bias {bias} for {pair}")
                return

            self.supabase.table("system_state").upsert({
                "pair": pair,
                "cot_bias": bias,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }).execute()
            logger.info(f"Updated Supabase state for {pair}: {bias}")
        except Exception as e:
            logger.error(f"Supabase update failed for {pair}: {e}")
            send_error_notification(f"COT Supabase Update Failed ({pair}): {e}")

    def run(self):
        """Main execution loop for COT reporting."""
        # 2026 Exact Market Names for CME FX Futures
        markets = {
            "EUR/USD": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
            "GBP/USD": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE"
        }
        
        report_text = "🏦 **SMART MONEY TRACKER (COT)** 🏦\n\n"
        
        for pair, market in markets.items():
            date, longs, shorts, net = self.get_cftc_data(market)
            
            if date:
                # LAYER 4: Trading Logic - Net position bias
                bias = "BULLISH" if net > 0 else "BEARISH"
                
                # Update DB
                self.update_system_state(pair, bias)
                
                # Format Report
                emoji = "🟢" if bias == "BULLISH" else "🔴"
                report_text += f"*{pair} Bias:* {emoji} {bias}\n"
                report_text += f"⚖️ **Net Position: {net:,}**\n"
                report_text += f"📅 Report Date: {date}\n\n"
            else:
                report_text += f"⚠️ *{pair}:* Data Unavailable\n\n"

        # LAYER 2: Separate try block for Telegram
        try:
            if self.bot:
                self.bot.send_message(
                    TELEGRAM_CHAT_ID, 
                    report_text, 
                    parse_mode="Markdown",
                    timeout=10
                )
        except Exception as e:
            logger.error(f"Telegram report failed: {e}")

if __name__ == "__main__":
    tracker = COTTracker()
    tracker.run()
