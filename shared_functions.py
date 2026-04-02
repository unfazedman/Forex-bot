import os
import telebot
from supabase import create_client, Client
from config import (
    WEIGHT_ATR, WEIGHT_SENTIMENT, WEIGHT_COT, 
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, 
    ERROR_BOT_TOKEN, ERROR_CHAT_ID,
    SUPABASE_URL, SUPABASE_KEY
)

def get_supabase_client() -> Client:
    """Returns an authenticated Supabase client."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise Exception("CRITICAL: Supabase URL or Key not found in environment.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def send_error_notification(error_message):
    """Sends a critical error alert to the dedicated Error Bot."""
    try:
        # Use the Error Bot if credentials are provided, otherwise fallback to main bot
        token = ERROR_BOT_TOKEN if ERROR_BOT_TOKEN else TELEGRAM_TOKEN
        chat_id = ERROR_CHAT_ID if ERROR_CHAT_ID else TELEGRAM_CHAT_ID
        
        if not token or not chat_id:
            print(f"No Telegram credentials found for error: {error_message}")
            return

        bot = telebot.TeleBot(token)
        bot.send_message(
            chat_id, 
            f"🚨 **SYSTEM CRITICAL ERROR** 🚨\n\n{error_message}",
            timeout=10,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Failed to send Telegram error alert: {e}")

def calculate_fusion_score(sentiment, atr_multiplier, cot_bias, pair_direction):
    """
    The Master Algorithm for calculating trade viability.
    """
    score = 50 
    
    # 1. Volatility Weight
    if atr_multiplier >= 1.5: 
        score += WEIGHT_ATR
        
    # 2. Macro Sentiment Weight (Directional Alignment)
    if pair_direction == "LONG":
        if sentiment >= 5: score += WEIGHT_SENTIMENT 
        elif sentiment <= -5: score -= WEIGHT_SENTIMENT 
    else: # SHORT
        if sentiment <= -5: score += WEIGHT_SENTIMENT   
        elif sentiment >= 5: score -= WEIGHT_SENTIMENT
        
    # 3. Hedge Fund Bias (COT)
    if cot_bias == "BULLISH" and pair_direction == "LONG": 
        score += WEIGHT_COT
    elif cot_bias == "BEARISH" and pair_direction == "SHORT": 
        score += WEIGHT_COT
    elif cot_bias != "NEUTRAL": 
        score -= WEIGHT_COT 
        
    return max(0, min(100, score))
