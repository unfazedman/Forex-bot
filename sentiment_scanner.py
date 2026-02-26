import requests
import feedparser
import telebot
import time
from datetime import datetime, timezone, timedelta
import os

# 1. Load keys from the GitHub Vault
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def score_headline(headline):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"You are a quantitative FX analyst. Read this headline: '{headline}'. Score its impact on the US Dollar from -10 (Highly Bearish) to +10 (Highly Bullish). Reply ONLY with the number. Do not add any text, explanations, or formatting."
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, json=payload).json()
        score_text = response['candidates'][0]['content']['parts'][0]['text'].strip()
        return int(score_text)
    except Exception:
        return 0

def scan_news():
    rss_url = "https://www.investing.com/rss/news_1.rss"
    feed = feedparser.parse(rss_url)
    
    # 2. The Filter: Calculate the exact time 20 minutes ago
    now_utc = datetime.now(timezone.utc)
    twenty_mins_ago = now_utc - timedelta(minutes=20)
    
    for entry in feed.entries:
        try:
            # Read the timestamp of the article
            article_time = datetime.fromtimestamp(time.mktime(entry.published_parsed), timezone.utc)
            
            # 3. Only process the headline if it was published in the last 20 minutes
            if article_time > twenty_mins_ago:
                headline = entry.title
                score = score_headline(headline)
                
                # 4. The Trigger
                if score >= 6:
                    bot.send_message(CHAT_ID, f"🚨 VOLATILITY ALERT: +{score} (Highly Bullish USD)\n📰 {headline}")
                elif score <= -6:
                    bot.send_message(CHAT_ID, f"🚨 VOLATILITY ALERT: {score} (Highly Bearish USD)\n📰 {headline}")
                
                time.sleep(2) # Pause briefly to respect API limits
        except Exception:
            continue

if __name__ == "__main__":
    scan_news()
  
