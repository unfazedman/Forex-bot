import os
import json
import gspread
import telebot
from config import WEIGHT_ATR, WEIGHT_SENTIMENT, WEIGHT_COT, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

def get_gspread_client():
    """Returns an authenticated Google Sheets client."""
    if os.path.exists('credentials.json'):
        return gspread.service_account(filename='credentials.json')
    
    creds_json = os.environ.get('GCP_CREDENTIALS')
    if creds_json:
        creds_dict = json.loads(creds_json)
        return gspread.service_account_from_dict(creds_dict)
        
    raise Exception("CRITICAL: Google Cloud Credentials not found.")

def send_error_notification(error_message):
    """Sends a critical error alert directly to the Telegram admin."""
    try:
        bot = telebot.TeleBot(TELEGRAM_TOKEN)
        bot.send_message(TELEGRAM_CHAT_ID, f"🚨 **SYSTEM CRITICAL ERROR** 🚨\n\n{error_message}")
    except Exception as e:
        print(f"Failed to send Telegram error alert: {e}")

def calculate_fusion_score(sentiment, atr_multiplier, cot_bias, pair_direction):
    """The Master Algorithm for calculating trade viability."""
    score = 50 
    
    if atr_multiplier >= 1.5: 
        score += WEIGHT_ATR
        
    if pair_direction == "LONG":
        if sentiment <= -5: score += WEIGHT_SENTIMENT 
        elif sentiment >= 5: score -= WEIGHT_SENTIMENT 
    else: 
        if sentiment >= 5: score += WEIGHT_SENTIMENT   
        elif sentiment <= -5: score -= WEIGHT_SENTIMENT
        
    if cot_bias == "BULLISH" and pair_direction == "LONG": 
        score += WEIGHT_COT
    elif cot_bias == "BEARISH" and pair_direction == "SHORT": 
        score += WEIGHT_COT
    elif cot_bias != "NEUTRAL": 
        score -= WEIGHT_COT 
        
    return max(0, min(100, score))
