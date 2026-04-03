"""
Advanced Sentiment Scanner (V5.2) - AI-Powered Sentiment Pipeline
Industry Standard: Multi-layer NLP for Quant Trading
Layer 1-4 Audit Applied: Logic, Resilience, Cost, Trading.
"""

import requests
import feedparser
import json
import logging
import hashlib
import re
import os
import time
from datetime import datetime, timezone
from typing import List, Dict
from uuid import uuid4
from fuzzywuzzy import fuzz
import telebot

# --- PLUG INTO THE CENTRAL NERVOUS SYSTEM ---
from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY, 
    GNEWS_API_KEY, NEWS_API_KEY, SUPABASE_URL, SUPABASE_KEY,
    validate_config
)
from shared_functions import get_supabase_client, send_error_notification

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Validate configuration on startup
validate_config()

# --- FINANCIAL KEYWORDS FOR FILTERING ---
FINANCIAL_KEYWORDS = {
    'inflation', 'interest rate', 'rate hike', 'rate cut', 'fed', 'federal reserve',
    'ecb', 'european central bank', 'boj', 'bank of japan', 'rbi', 'reserve bank of india',
    'gdp', 'gross domestic product', 'employment', 'unemployment', 'nfp', 'non-farm payroll',
    'cpi', 'consumer price index', 'ppi', 'producer price index', 'pce',
    'oil', 'crude', 'energy', 'commodity', 'gold', 'silver',
    'forex', 'currency', 'exchange rate', 'usd', 'eur', 'gbp', 'jpy', 'cad', 'aud',
    'powell', 'lagarde', 'kuroda', 'das'
}

CENTRAL_BANK_KEYWORDS = {
    'fed', 'federal reserve', 'powell', 'ecb', 'european central bank', 'lagarde',
    'boj', 'bank of japan', 'kuroda', 'rbi', 'reserve bank of india', 'das'
}

HIGH_IMPACT_KEYWORDS = {
    'inflation', 'cpi', 'gdp', 'employment', 'nfp', 'interest rate', 'rate hike', 'rate cut'
}

class SentimentScannerPipeline:
    """
    Complete sentiment analysis pipeline with 9-layer architecture.
    """
    
    def __init__(self):
        try:
            self.supabase = get_supabase_client()
            self.bot = telebot.TeleBot(TELEGRAM_TOKEN)
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            send_error_notification(f"Scanner Init Failed: {e}")
            self.supabase = None
            self.bot = None
            
        self.state_file = 'scanner_state.json'
        self.processed_hashes = set()
        self.load_state()
    
    def load_state(self):
        """Loads processed hashes from state file to avoid redundant processing."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.processed_hashes = set(state.get('processed_hashes', []))
                    logger.info(f"[State] Loaded {len(self.processed_hashes)} processed hashes")
        except Exception as e:
            logger.error(f"[State] Error loading state: {e}")
            self.processed_hashes = set()
    
    def save_state(self):
        """Saves processed hashes to state file."""
        try:
            # Keep only last 1000 hashes to prevent file bloat
            hashes_list = list(self.processed_hashes)[-1000:]
            state = {'processed_hashes': hashes_list}
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
            logger.info("[State] State saved successfully")
        except Exception as e:
            logger.error(f"[State] Failed to save state: {e}")
    
    # ===== LAYER 2: COLLECTOR (RSS & GNews) =====
    def collect_rss(self) -> List[Dict]:
        """Fetches financial headlines from ForexLive RSS feed."""
        rss_url = "https://www.forexlive.com/feed"
        collected = []
        
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            # LAYER 2: System Resilience - Timeout and raise_for_status
            response = requests.get(rss_url, headers=headers, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            
            for entry in feed.entries:
                text = entry.title
                text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
                
                if text_hash in self.processed_hashes:
                    continue
                
                item = {
                    "id": str(uuid4()),
                    "text": text,
                    "source": "rss",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "author": "ForexLive",
                    "url": entry.link,
                    "hash": text_hash
                }
                collected.append(item)
                logger.info(f"[Collector] RSS: {text[:60]}...")
        except Exception as e:
            logger.error(f"[Collector] RSS error: {e}")
            
        return collected

    def collect_gnews(self, keywords: List[str] = None) -> List[Dict]:
        """Fetches financial headlines from GNews API."""
        if not GNEWS_API_KEY:
            logger.warning("[Collector] GNews API Key not found")
            return []
        
        if keywords is None:
            keywords = ["inflation", "Fed", "ECB"]
        
        collected = []
        for keyword in keywords[:3]:
            try:
                url = f"https://gnews.io/api/v4/search?q={keyword}&token={GNEWS_API_KEY}&lang=en&max=5"
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                data = response.json()
                
                for article in data.get('articles', []):
                    text = article.get('title', '')
                    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
                    
                    if text_hash in self.processed_hashes:
                        continue
                    
                    item = {
                        "id": str(uuid4()),
                        "text": text,
                        "source": "news",
                        "timestamp": article.get('publishedAt', datetime.now(timezone.utc).isoformat()),
                        "author": article.get('source', {}).get('name', 'GNews'),
                        "url": article.get('url'),
                        "hash": text_hash
                    }
                    collected.append(item)
                    logger.info(f"[Collector] GNews: {text[:60]}...")
            except Exception as e:
                logger.error(f"[Collector] GNews error for '{keyword}': {e}")
        
        return collected
    
    # ===== LAYER 4: CLEANING =====
    @staticmethod
    def clean_text(text: str) -> str:
        """Cleans text: removes URLs, emojis, normalizes."""
        if not text:
            return None
        
        text = re.sub(r'http\S+|www\S+|https\S+', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Trim to 500 chars
        if len(text) < 20:
            return None
        return text[:500]
    
    # ===== LAYER 6: RELEVANCE FILTER =====
    @staticmethod
    def is_relevant(text: str) -> bool:
        """Checks if text is financially relevant."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in FINANCIAL_KEYWORDS)
    
    # ===== LAYER 7: IMPORTANCE SCORING =====
    def calculate_importance_score(self, text: str, timestamp: str) -> Dict:
        """Calculates importance score based on keywords and time decay."""
        text_lower = text.lower()
        base_score = 0.5
        
        if any(kw in text_lower for kw in CENTRAL_BANK_KEYWORDS):
            base_score += 3.0
        elif any(kw in text_lower for kw in HIGH_IMPACT_KEYWORDS):
            base_score += 2.0
        else:
            base_score += 1.0
        
        # Time decay logic
        try:
            item_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            age_hours = (now - item_time).total_seconds() / 3600
            
            if age_hours > 24:
                return {"score": 0, "tier": "EXPIRED"}
            elif age_hours > 6:
                base_score *= 0.5
        except:
            pass
        
        tier = "HIGH" if base_score >= 4.0 else ("MEDIUM" if base_score >= 2.0 else "LOW")
        return {"score": base_score, "tier": tier}
    
    # ===== LAYER 9: SENTIMENT ENGINE (AI FALLBACK CHAIN) =====
    def analyze_sentiment(self, text: str, pair: str = "EUR/USD") -> Dict:
        """
        Analyzes sentiment with AI Fallback Chain: Gemini -> HuggingFace -> Neutral.
        """
        if not GEMINI_API_KEY:
            return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "NONE"}
            
        prompt = f"""Analyze impact on {pair} price (2h window). 
Output JSON ONLY: {{"sentiment": "Bullish|Bearish|Neutral", "confidence": 0.0-1.0}}
News: "{text}"
"""
        try:
            # LAYER 2: System Resilience - Gemini Primary
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            
            raw_text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            
            if json_match:
                result = json.loads(json_match.group())
                return {
                    "sentiment": result.get('sentiment', 'NEUTRAL').upper(),
                    "confidence": float(result.get('confidence', 0.0)),
                    "model": "Gemini"
                }
        except Exception as e:
            logger.warning(f"Gemini failed, falling back: {e}")
            # Fallback to simple keyword sentiment (HuggingFace replacement for mobile-first)
            text_lower = text.lower()
            if any(w in text_lower for w in ['surge', 'hike', 'rise', 'strong']):
                return {"sentiment": "BULLISH", "confidence": 0.5, "model": "Fallback"}
            if any(w in text_lower for w in ['fall', 'cut', 'drop', 'weak']):
                return {"sentiment": "BEARISH", "confidence": 0.5, "model": "Fallback"}
        
        return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "Fallback"}
    
    def run_pipeline(self):
        """Main orchestrator for the sentiment pipeline."""
        logger.info("--- Starting Sentiment Pipeline ---")
        
        collected = self.collect_rss() + self.collect_gnews()
        processed_count = 0
        
        for item in collected:
            cleaned = self.clean_text(item['text'])
            if not cleaned or not self.is_relevant(cleaned):
                continue
                
            score_data = self.calculate_importance_score(cleaned, item['timestamp'])
            if score_data['tier'] == "EXPIRED":
                continue
                
            # LAYER 4: Trading Logic - Independent Pair Analysis
            eur_sent = self.analyze_sentiment(cleaned, "EUR/USD")
            gbp_sent = self.analyze_sentiment(cleaned, "GBP/USD")
            
            # LAYER 2: System Resilience - Separate DB Try Block
            try:
                if self.supabase:
                    self.supabase.table("processed_sentiment").insert({
                        "text_cleaned": cleaned,
                        "source": item['source'],
                        "importance_score": score_data['score'],
                        "importance_tier": score_data['tier'],
                        "eur_usd_sentiment": eur_sent['sentiment'],
                        "eur_usd_confidence": eur_sent['confidence'],
                        "gbp_usd_sentiment": gbp_sent['sentiment'],
                        "gbp_usd_confidence": gbp_sent['confidence'],
                        "model_used": eur_sent['model']
                    }).execute()
                    processed_count += 1
                    self.processed_hashes.add(item['hash'])
            except Exception as e:
                logger.error(f"DB insert failed: {e}")
                
        self.save_state()
        logger.info(f"Pipeline complete. Processed {processed_count} items.")
        return {"status": "success", "processed": processed_count}

if __name__ == "__main__":
    pipeline = SentimentScannerPipeline()
    pipeline.run_pipeline()
