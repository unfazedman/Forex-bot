import requests
import feedparser
import telebot
import time
import os
import re
import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
STATE_FILE = 'state.json'

# 1. Atomic State Management
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading state: {e}")
    # Added 'last_shift_alert' for the 60-minute cooldown
    return {"processed_hashes": [], "momentum": [], "last_shift_alert": 0}

def save_state(state):
    # ATOMIC WRITE: Prevents JSON corruption if the GitHub server crashes mid-save
    tmp_file = f"{STATE_FILE}.tmp"
    with open(tmp_file, 'w') as f:
        json.dump(state, f)
    os.replace(tmp_file, STATE_FILE)

def score_headline(headline):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"Score the direct impact of this headline on EUR/USD price over the next 2 hours from -10 (Highly Bearish USD) to +10 (Highly Bullish USD). Output only an integer. Headline: '{headline}'"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0} 
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status() 
        raw_text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        
        match = re.search(r'-?\d+', raw_text)
        return int(match.group()) if match else 0
    except Exception as e:
        logging.error(f"AI API Error: {e}")
        return 0

def scan_news():
    state = load_state()
    
    # Upgraded Data Source: Yahoo Finance Forex Feed (Better institutional flow than Investing.com)
    rss_url = "https://feeds.finance.yahoo.com/rss/2.0/category-forex-and-currencies"
    
    try:
        raw_feed = requests.get(rss_url, timeout=10)
        raw_feed.raise_for_status()
        feed = feedparser.parse(raw_feed.content)
    except Exception as e:
        logging.error(f"RSS Fetch Error: {e}")
        return

    keywords = ['Fed', 'Tariffs', 'Inflation', 'BOJ', 'Treasury', 'CPI', 'NFP', 'Trump', 'Rate', 'ECB', 'Powell']
    now_utc = datetime.now(timezone.utc)
    current_time = now_utc.timestamp()
    new_events_processed = False

    for entry in feed.entries:
        headline = entry.title
        
        # 2. MD5 Deduplication Hash
        hl_hash = hashlib.md5(headline.encode('utf-8')).hexdigest()
        
        if hl_hash in state['processed_hashes']:
            continue
            
        if not any(kw.lower() in headline.lower() for kw in keywords):
            continue
            
        logging.info(f"Scoring: {headline}")
        score = score_headline(headline)
        
        state['processed_hashes'].append(hl_hash)
        if len(state['processed_hashes']) > 200:
            state['processed_hashes'].pop(0)
            
        new_events_processed = True

        if score >= 6 or score <= -6:
            direction = "Bullish" if score > 0 else "Bearish"
            bot.send_message(CHAT_ID, f"🚨 MACRO VOLATILITY: {score} ({direction} USD)\n📰 {headline}")

        if abs(score) >= 3:
            state['momentum'].append({"time": current_time, "score": score, "headline": headline})
            
        time.sleep(2) 

    # 3. The Flawless Momentum Logic (Cooldown, no array wiping)
    state['momentum'] = [m for m in state['momentum'] if current_time - m['time'] <= 5400]
    
    if len(state['momentum']) >= 3:
        avg_score = sum(m['score'] for m in state['momentum']) / len(state['momentum'])
        
        # Check if 60 minutes (3600s) have passed since the last shift alert
        if (avg_score >= 5.0 or avg_score <= -5.0) and (current_time - state.get('last_shift_alert', 0) > 3600):
            direction = "BULLISH" if avg_score > 0 else "BEARISH"
            cluster_text = "\n".join([f"- {m['headline']} ({m['score']})" for m in state['momentum']])
            
            bot.send_message(CHAT_ID, f"⚠️ USD NARRATIVE SHIFT DETECTED ⚠️\nDirection: {direction} (Avg Score: {avg_score:.1f})\n\nCatalysts in last 90 mins:\n{cluster_text}")
            
            # Apply cooldown timestamp. We DO NOT clear the momentum array anymore.
            state['last_shift_alert'] = current_time
            new_events_processed = True

    if new_events_processed:
        save_state(state)
        logging.info("State successfully saved.")

if __name__ == "__main__":
    scan_news()
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
  
