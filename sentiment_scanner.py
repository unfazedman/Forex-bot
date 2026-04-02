"""
Advanced Sentiment Scanner - Main Pipeline Orchestrator (V5.1)
Runs sequential layers: Collector (RSS) → Cleaner → Deduplicator → Filter → Scorer → Router → Sentiment Engine
Optimized for GitHub Actions: Removed snscrape due to instability/dependency issues.
"""

import requests
import feedparser
import json
import logging
import hashlib
import re
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
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Validate configuration on startup
try:
    validate_config()
    logger.info("[STARTUP] Configuration validated successfully")
except EnvironmentError as e:
    logger.error(f"[STARTUP] Configuration error: {e}")
    # We don't raise here to allow GitHub Actions to run, but we log the error.

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
    Complete sentiment analysis pipeline with all layers integrated.
    """
    
    def __init__(self):
        try:
            self.supabase = get_supabase_client()
        except Exception as e:
            logger.error(f"Failed to connect to Supabase: {e}")
            self.supabase = None
            
        self.state_file = 'scanner_state.json'
        self.processed_hashes = set()
        self.load_state()
    
    def load_state(self):
        """Loads processed hashes from state file to avoid reprocessing."""
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
            state = {'processed_hashes': list(self.processed_hashes)}
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
            raw_feed = requests.get(rss_url, headers=headers, timeout=10)
            raw_feed.raise_for_status()
            feed = feedparser.parse(raw_feed.content)
            
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
                    "engagement": {"likes": 0, "retweets": 0},
                    "url": entry.link,
                    "hash": text_hash
                }
                collected.append(item)
                self.processed_hashes.add(text_hash)
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
            keywords = ["inflation", "interest rate", "Fed", "ECB", "forex"]
        
        collected = []
        for keyword in keywords[:3]:  # Limit to 3 to avoid rate limits
            try:
                url = f"https://gnews.io/api/v4/search?q={keyword}&token={GNEWS_API_KEY}&lang=en&sortby=publishedAt&max=5"
                response = requests.get(url, timeout=10)
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
                        "engagement": {"likes": 0, "retweets": 0},
                        "url": article.get('url'),
                        "hash": text_hash
                    }
                    collected.append(item)
                    self.processed_hashes.add(text_hash)
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
        
        # Remove URLs
        text = re.sub(r'http\S+|www\S+|https\S+', '', text)
        
        # Remove emojis
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\u2600-\u2B55"
            "]+",
            flags=re.UNICODE
        )
        text = emoji_pattern.sub(r'', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Lowercase
        text = text.lower()
        
        # Trim to 300-500 chars
        if len(text) < 20:
            return None
        if len(text) > 500:
            text = text[:500].rsplit(' ', 1)[0]
        
        return text
    
    # ===== LAYER 5: DEDUPLICATION =====
    @staticmethod
    def calculate_similarity(text1: str, text2: str) -> float:
        """Calculates text similarity."""
        return fuzz.token_set_ratio(text1, text2) / 100.0
    
    # ===== LAYER 6: RELEVANCE FILTER =====
    @staticmethod
    def is_relevant(text: str) -> bool:
        """Checks if text is financially relevant."""
        text_lower = text.lower()
        
        # Financial keywords
        for keyword in FINANCIAL_KEYWORDS:
            if keyword in text_lower:
                return True
        
        return False
    
    # ===== LAYER 7: IMPORTANCE SCORING =====
    def calculate_importance_score(self, text: str, timestamp: str) -> Dict:
        """Calculates importance score."""
        text_lower = text.lower()
        base_score = 0.5
        
        # Central bank keywords
        if any(kw in text_lower for kw in CENTRAL_BANK_KEYWORDS):
            base_score += 3.0
        elif any(kw in text_lower for kw in HIGH_IMPACT_KEYWORDS):
            base_score += 2.0
        else:
            base_score += 1.0
        
        # Time decay
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
        
        # Determine tier
        if base_score >= 4.0:
            tier = "HIGH"
        elif base_score >= 2.0:
            tier = "MEDIUM"
        else:
            tier = "LOW"
        
        return {"score": base_score, "tier": tier}
    
    # ===== LAYER 8: AI ROUTER =====
    def route_to_model(self, importance_tier: str, text_length: int) -> str:
        """Routes to HuggingFace or Gemini."""
        if importance_tier == "HIGH":
            return "Gemini"
        elif importance_tier == "MEDIUM" and text_length > 100:
            return "Gemini"
        else:
            return "HuggingFace"
    
    # ===== LAYER 9: SENTIMENT ENGINE =====
    def analyze_with_gemini(self, text: str, pair: str = "EUR/USD") -> Dict:
        """Analyzes sentiment using Gemini API."""
        if not GEMINI_API_KEY:
            logger.warning("[Sentiment] Gemini API Key not found")
            return {"sentiment": "NEUTRAL", "confidence": 0.0}
        
        prompt = f"""Analyze this financial news for its impact on {pair} price over the next 2 hours.
Output ONLY valid JSON (no markdown):
{{
  "sentiment": "Bullish|Bearish|Neutral",
  "confidence": 0.0-1.0
}}

News: "{text[:500]}"
"""
        
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3}
            }
            
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            
            raw_text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            
            if json_match:
                result = json.loads(json_match.group())
                sentiment = result.get('sentiment', 'NEUTRAL').upper()
                confidence = float(result.get('confidence', 0.0))
                
                logger.info(f"[Gemini] {pair}: {sentiment} (confidence: {confidence:.2f})")
                return {"sentiment": sentiment, "confidence": confidence, "model": "Gemini"}
        except Exception as e:
            logger.error(f"[Sentiment] Gemini error: {e}")
        
        return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "Gemini"}
    
    # ===== MAIN PIPELINE =====
    def run_pipeline(self) -> Dict:
        """Runs the complete sentiment analysis pipeline."""
        logger.info("=" * 60)
        logger.info("Starting Sentiment Scanner Pipeline")
        logger.info("=" * 60)
        
        try:
            # LAYER 2: Collect
            rss_items = self.collect_rss()
            gnews_items = self.collect_gnews()
            all_items = rss_items + gnews_items
            logger.info(f"[Pipeline] Collected {len(all_items)} items")
            
            if not all_items:
                logger.info("[Pipeline] No new items to process")
                return {"status": "success", "items_processed": 0}
            
            # LAYER 4: Clean
            cleaned_items = []
            for item in all_items:
                cleaned_text = self.clean_text(item['text'])
                if cleaned_text:
                    item['text_cleaned'] = cleaned_text
                    cleaned_items.append(item)
            logger.info(f"[Pipeline] Cleaned {len(cleaned_items)} items")
            
            # LAYER 6: Filter
            filtered_items = [item for item in cleaned_items if self.is_relevant(item['text_cleaned'])]
            logger.info(f"[Pipeline] Filtered to {len(filtered_items)} relevant items")
            
            # LAYER 7: Score
            scored_items = []
            for item in filtered_items:
                score_result = self.calculate_importance_score(item['text_cleaned'], item['timestamp'])
                if score_result['tier'] != "EXPIRED":
                    item['importance_score'] = score_result['score']
                    item['importance_tier'] = score_result['tier']
                    scored_items.append(item)
            logger.info(f"[Pipeline] Scored {len(scored_items)} items")
            
            # LAYER 8: Route
            for item in scored_items:
                model = self.route_to_model(item['importance_tier'], len(item['text_cleaned']))
                item['model_assigned'] = model
            
            # LAYER 9: Sentiment Analysis
            processed_count = 0
            for item in scored_items:
                try:
                    eur_sentiment = self.analyze_with_gemini(item['text_cleaned'], "EUR/USD")
                    gbp_sentiment = self.analyze_with_gemini(item['text_cleaned'], "GBP/USD")
                    
                    # Store in Supabase
                    if self.supabase:
                        payload = {
                            "text_cleaned": item['text_cleaned'],
                            "source": item['source'],
                            "timestamp": item['timestamp'],
                            "author": item['author'],
                            "engagement": item['engagement'],
                            "url": item['url'],
                            "importance_score": item['importance_score'],
                            "importance_tier": item['importance_tier'],
                            "eur_usd_sentiment": eur_sentiment['sentiment'],
                            "eur_usd_confidence": eur_sentiment['confidence'],
                            "gbp_usd_sentiment": gbp_sentiment['sentiment'],
                            "gbp_usd_confidence": gbp_sentiment['confidence'],
                            "model_used": item['model_assigned']
                        }
                        self.supabase.table("processed_sentiment").insert(payload).execute()
                        processed_count += 1
                        logger.info(f"[Pipeline] Stored sentiment: {item['text_cleaned'][:60]}...")
                    
                except Exception as e:
                    logger.error(f"[Pipeline] Failed to process item: {e}")
            
            # Save state
            self.save_state()
            
            result = {
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "collected": len(all_items),
                "cleaned": len(cleaned_items),
                "filtered": len(filtered_items),
                "scored": len(scored_items),
                "processed": processed_count
            }
            
            logger.info(f"[Pipeline] Cycle complete: {result}")
            return result
            
        except Exception as e:
            error_msg = f"Pipeline error: {str(e)}"
            logger.error(error_msg)
            send_error_notification(error_msg)
            return {"status": "error", "error": str(e)}


def main():
    """Main entry point."""
    import os
    pipeline = SentimentScannerPipeline()
    result = pipeline.run_pipeline()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
